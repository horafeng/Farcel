import hashlib
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from farcel.application.project_assets import (
    ProjectAssetValidator,
    _is_within_root,
)
from farcel.contracts import ModelAsset, SimulationProject
from farcel.infrastructure.project import LocalJsonProjectRepository


class ProjectAssetValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "Project"
        self.root.mkdir()
        self.validator = ProjectAssetValidator()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_valid_asset_resolves_existing_file_and_checksum(self) -> None:
        asset_path = _write_asset(self.root, "models/plant.fmu", b"plant")
        asset = _asset("models/plant.fmu", _sha256(asset_path))

        check = self.validator.check(self.root, asset)

        self.assertTrue(check.report.is_valid)
        self.assertEqual(check.resolved_path, asset_path.resolve())

    def test_valid_nested_relative_path_does_not_require_models_prefix(self) -> None:
        asset_path = _write_asset(self.root, "vendor/models/plant.fmu", b"plant")

        check = self.validator.check(
            self.root, _asset("vendor/models/plant.fmu", _sha256(asset_path))
        )

        self.assertTrue(check.report.is_valid)
        self.assertEqual(check.resolved_path, asset_path.resolve())

    def test_invalid_relative_paths_are_rejected_before_filesystem_access(self) -> None:
        invalid_paths = (
            "",
            ".",
            "..",
            "../plant.fmu",
            "models/../plant.fmu",
            "models/../../plant.fmu",
            "/models/plant.fmu",
            "C:/models/plant.fmu",
            r"C:\models\plant.fmu",
            r"\\server\share\plant.fmu",
            "//server/share/plant.fmu",
            r"models\plant.fmu",
            "models//plant.fmu",
            "models/./plant.fmu",
        )
        for relative_path in invalid_paths:
            with self.subTest(relative_path=relative_path):
                with patch.object(
                    ProjectAssetValidator,
                    "_sha256",
                    side_effect=AssertionError("invalid path must not be read"),
                ):
                    check = self.validator.check(
                        self.root, _asset(relative_path, "0" * 64)
                    )
                self.assertEqual(_issue_codes(check), ("PROJECT_PATH_INVALID",))
                self.assertIsNone(check.resolved_path)

    def test_missing_asset_is_reported_without_exception(self) -> None:
        check = self.validator.check(
            self.root, _asset("models/missing.fmu", "0" * 64)
        )

        self.assertEqual(_issue_codes(check), ("ASSET_MISSING",))
        self.assertIsNone(check.resolved_path)

    def test_directory_asset_is_not_hashed_as_a_file(self) -> None:
        (self.root / "models").mkdir()

        check = self.validator.check(self.root, _asset("models", "0" * 64))

        self.assertEqual(_issue_codes(check), ("ASSET_NOT_FILE",))

    def test_invalid_and_uppercase_sha256_are_rejected(self) -> None:
        asset_path = _write_asset(self.root, "models/plant.fmu", b"plant")
        for digest in ("abc", _sha256(asset_path).upper()):
            with self.subTest(digest=digest):
                check = self.validator.check(self.root, _asset("models/plant.fmu", digest))
                self.assertEqual(_issue_codes(check), ("ASSET_SHA256_INVALID",))

    def test_checksum_mismatch_after_asset_mutation_is_reported(self) -> None:
        asset_path = _write_asset(self.root, "models/plant.fmu", b"original")
        asset = _asset("models/plant.fmu", _sha256(asset_path))
        self.assertTrue(self.validator.check(self.root, asset).report.is_valid)

        asset_path.write_bytes(b"mutated")

        check = self.validator.check(self.root, asset)
        self.assertEqual(_issue_codes(check), ("ASSET_CHECKSUM_MISMATCH",))
        self.assertIsNone(check.resolved_path)

    def test_file_read_error_is_reported_as_validation_issue(self) -> None:
        asset_path = _write_asset(self.root, "models/plant.fmu", b"plant")
        with patch.object(ProjectAssetValidator, "_sha256", side_effect=OSError("denied")):
            check = self.validator.check(
                self.root, _asset("models/plant.fmu", _sha256(asset_path))
            )

        self.assertEqual(_issue_codes(check), ("ASSET_READ_ERROR",))

    def test_resolved_outside_root_is_rejected_by_containment_helper(self) -> None:
        outside = self.root.parent / "outside.fmu"
        outside.write_bytes(b"outside")

        self.assertFalse(_is_within_root(self.root.resolve(), outside.resolve()))

    def test_symlink_escape_is_rejected_when_supported(self) -> None:
        outside = self.root.parent / "outside.fmu"
        outside.write_bytes(b"outside")
        link = self.root / "models" / "external.fmu"
        link.parent.mkdir()
        try:
            os.symlink(outside, link)
        except OSError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")

        check = self.validator.check(self.root, _asset("models/external.fmu", "0" * 64))

        self.assertEqual(_issue_codes(check), ("PROJECT_PATH_ESCAPE",))
        self.assertIsNone(check.resolved_path)

    def test_repository_round_trip_keeps_semantic_invalid_asset_declarative(self) -> None:
        project = SimulationProject(
            "project",
            "Demo",
            model_assets=(ModelAsset("asset", "Asset", "../bad.fmu", "bad"),),
        )
        repository = LocalJsonProjectRepository()

        repository.save(self.root, project)

        loaded = repository.load(self.root)
        self.assertEqual(loaded, project)
        self.assertEqual(
            _issue_codes(self.validator.check(self.root, loaded.model_assets[0])),
            ("PROJECT_PATH_INVALID",),
        )

    def test_project_relocation_preserves_relative_asset_resolution_and_checksum(self) -> None:
        location_a = self.root.parent / "LocationA"
        location_b = self.root.parent / "LocationB"
        asset_path = _write_asset(location_a, "models/plant.fmu", b"relocatable")
        project = SimulationProject(
            "project",
            "Demo",
            model_assets=(
                _asset("models/plant.fmu", _sha256(asset_path)),
            ),
        )
        repository = LocalJsonProjectRepository()
        repository.save(location_a, project)

        shutil.copytree(location_a, location_b)

        loaded = repository.load(location_b)
        check = self.validator.check(location_b, loaded.model_assets[0])

        self.assertTrue(check.report.is_valid)
        self.assertEqual(check.resolved_path, (location_b / "models/plant.fmu").resolve())
        self.assertTrue(_is_within_root(location_b.resolve(), check.resolved_path))
        self.assertNotIn(
            str(location_a.resolve()),
            (location_b / "project.json").read_text(encoding="utf-8"),
        )


def _asset(relative_path: str, sha256: str) -> ModelAsset:
    return ModelAsset("asset", "Asset", relative_path, sha256)


def _write_asset(root: Path, relative_path: str, contents: bytes) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _issue_codes(check) -> tuple[str, ...]:
    return tuple(issue.code for issue in check.report.issues)
