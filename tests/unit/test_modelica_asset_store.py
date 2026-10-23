from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from farcel.contracts import EngineError, ErrorCode
from farcel.infrastructure.project.modelica_asset_store import LocalModelicaAssetStore


_BUILD_ID = "a" * 32


class LocalModelicaAssetStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary_directory.name) / "Project"
        self.root.mkdir()
        self.source = Path(self._temporary_directory.name) / "Source.mo"
        self.source.write_text("model Source end Source;", encoding="utf-8")
        self.store = LocalModelicaAssetStore()

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_snapshots_source_and_publishes_verified_fmu_as_one_build_directory(self) -> None:
        workspace = self.store.prepare_source_snapshot(self.root, self.source, _BUILD_ID)
        fmu = workspace.compiler_output_directory / "Source.fmu"
        fmu.write_bytes(b"verified-fmu")
        (workspace.compiler_output_directory / "intermediate.c").write_text(
            "temporary C source", encoding="utf-8"
        )
        (workspace.compiler_output_directory / "compile.log").write_text(
            "temporary log", encoding="utf-8"
        )
        (workspace.compiler_output_directory / "temporary.bin").write_bytes(b"temporary")
        digest = _sha256(fmu)

        published = self.store.publish_verified_build(workspace, fmu, digest)

        self.assertEqual(
            f"modelica-builds/build-{_BUILD_ID}/source/model.mo",
            workspace.source_snapshot.relative_path,
        )
        saved_source = published.parent.parent / "source" / "model.mo"
        self.assertEqual(_sha256(saved_source), workspace.source_snapshot.sha256)
        self.assertEqual(b"verified-fmu", published.read_bytes())
        self.assertEqual(digest, _sha256(published))
        self.assertFalse(workspace.workspace_path.exists())
        self.assertTrue(saved_source.is_file())
        final_directory = published.parent.parent
        self.assertTrue((final_directory / ".farcel-modelica-staging").is_file())
        self.assertFalse((final_directory / "compiler-output").exists())
        self.assertEqual(
            {".farcel-modelica-staging", "source", "model"},
            {entry.name for entry in final_directory.iterdir()},
        )
        self.assertEqual({"model.mo"}, {entry.name for entry in saved_source.parent.iterdir()})
        self.assertEqual({"model.fmu"}, {entry.name for entry in published.parent.iterdir()})

    def test_source_change_during_copy_is_rejected_and_owned_workspace_is_cleaned(self) -> None:
        original_copy = __import__(
            "farcel.infrastructure.project.modelica_asset_store", fromlist=["_copy_file_exclusive"]
        )._copy_file_exclusive

        def copy_then_mutate(source: Path, destination: Path) -> None:
            original_copy(source, destination)
            source.write_text("model Changed end Changed;", encoding="utf-8")

        with patch(
            "farcel.infrastructure.project.modelica_asset_store._copy_file_exclusive",
            side_effect=copy_then_mutate,
        ):
            with self.assertRaises(EngineError) as raised:
                self.store.prepare_source_snapshot(self.root, self.source, _BUILD_ID)

        self.assertEqual(ErrorCode.MODELICA_STAGING_ERROR, raised.exception.code)
        self.assertFalse((self.root / ".farcel-modelica-staging").exists())

    def test_rejects_fmu_outside_current_compiler_output_and_hash_mismatch(self) -> None:
        workspace = self.store.prepare_source_snapshot(self.root, self.source, _BUILD_ID)
        outside = Path(self._temporary_directory.name) / "outside.fmu"
        outside.write_bytes(b"outside")
        with self.assertRaises(EngineError) as outside_error:
            self.store.publish_verified_build(workspace, outside, _sha256(outside))
        self.assertEqual(ErrorCode.MODELICA_STAGING_ERROR, outside_error.exception.code)

        fmu = workspace.compiler_output_directory / "Source.fmu"
        fmu.write_bytes(b"inside")
        with self.assertRaises(EngineError) as digest_error:
            self.store.publish_verified_build(workspace, fmu, "b" * 64)
        self.assertEqual(ErrorCode.MODELICA_STAGING_ERROR, digest_error.exception.code)

    def test_existing_formal_build_directory_is_never_overwritten(self) -> None:
        workspace = self.store.prepare_source_snapshot(self.root, self.source, _BUILD_ID)
        fmu = workspace.compiler_output_directory / "Source.fmu"
        fmu.write_bytes(b"new")
        existing = self.root / "modelica-builds" / f"build-{_BUILD_ID}"
        existing.mkdir(parents=True)
        sentinel = existing / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")

        with self.assertRaises(EngineError) as raised:
            self.store.publish_verified_build(workspace, fmu, _sha256(fmu))

        self.assertEqual(ErrorCode.MODELICA_OUTPUT_CONFLICT, raised.exception.code)
        self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))
        self.store.discard_workspace(workspace)

    def test_partial_publish_failure_only_leaves_a_cleanable_owned_workspace(self) -> None:
        workspace = self.store.prepare_source_snapshot(self.root, self.source, _BUILD_ID)
        fmu = workspace.compiler_output_directory / "Source.fmu"
        fmu.write_bytes(b"new")
        with patch(
            "farcel.infrastructure.project.modelica_asset_store._copy_file_exclusive",
            side_effect=OSError("simulated write failure"),
        ):
            with self.assertRaises(EngineError) as raised:
                self.store.publish_verified_build(workspace, fmu, _sha256(fmu))

        self.assertEqual(ErrorCode.MODELICA_STAGING_ERROR, raised.exception.code)
        self.assertFalse((self.root / "modelica-builds").exists())
        self.store.discard_workspace(workspace)
        self.assertFalse(workspace.workspace_path.exists())

    def test_compiler_output_cleanup_failure_prevents_publication_and_preserves_other_builds(self) -> None:
        workspace = self.store.prepare_source_snapshot(self.root, self.source, _BUILD_ID)
        fmu = workspace.compiler_output_directory / "Source.fmu"
        fmu.write_bytes(b"new")
        other = self.root / "modelica-builds" / ("build-" + "b" * 32)
        other.mkdir(parents=True)
        sentinel = other / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        original_rmtree = shutil.rmtree

        def refuse_current_output(path: str | Path, *args: object, **kwargs: object) -> None:
            if Path(path) == workspace.compiler_output_directory:
                raise OSError("simulated cleanup failure")
            original_rmtree(path, *args, **kwargs)

        with patch(
            "farcel.infrastructure.project.modelica_asset_store.shutil.rmtree",
            side_effect=refuse_current_output,
        ):
            with self.assertRaises(EngineError) as raised:
                self.store.publish_verified_build(workspace, fmu, _sha256(fmu))

        self.assertEqual(ErrorCode.MODELICA_STAGING_ERROR, raised.exception.code)
        self.assertFalse((self.root / "modelica-builds" / f"build-{_BUILD_ID}").exists())
        self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))
        self.assertTrue(workspace.workspace_path.is_dir())
        self.store.discard_workspace(workspace)

    def test_cleanup_only_removes_marker_owned_workspace(self) -> None:
        workspace = self.store.prepare_source_snapshot(self.root, self.source, _BUILD_ID)
        self.store.discard_workspace(workspace)
        self.assertFalse(workspace.workspace_path.exists())

        unknown = self.root / ".farcel-modelica-staging" / "build-unknown"
        unknown.mkdir(parents=True)
        (unknown / "keep.txt").write_text("keep", encoding="utf-8")
        self.store.discard_published_build(self.root, _BUILD_ID)
        self.assertTrue(unknown.is_dir())

    def test_source_symlink_is_rejected_when_supported(self) -> None:
        linked = Path(self._temporary_directory.name) / "linked.mo"
        try:
            linked.symlink_to(self.source)
        except OSError as error:
            self.skipTest(f"symlink creation is unavailable: {error}")

        with self.assertRaises(EngineError) as raised:
            self.store.prepare_source_snapshot(self.root, linked, _BUILD_ID)

        self.assertEqual(ErrorCode.MODELICA_STAGING_ERROR, raised.exception.code)

    def test_symlinked_formal_build_parent_is_rejected_when_supported(self) -> None:
        outside = Path(self._temporary_directory.name) / "Outside"
        outside.mkdir()
        target = self.root / "modelica-builds"
        try:
            target.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlink creation is unavailable: {error}")
        workspace = self.store.prepare_source_snapshot(self.root, self.source, _BUILD_ID)
        fmu = workspace.compiler_output_directory / "Source.fmu"
        fmu.write_bytes(b"new")

        with self.assertRaises(EngineError) as raised:
            self.store.publish_verified_build(workspace, fmu, _sha256(fmu))

        self.assertEqual(ErrorCode.MODELICA_STAGING_ERROR, raised.exception.code)
        self.assertEqual([], list(outside.iterdir()))
        self.store.discard_workspace(workspace)

    def test_symlinked_compiler_output_is_never_removed_when_supported(self) -> None:
        workspace = self.store.prepare_source_snapshot(self.root, self.source, _BUILD_ID)
        outside = Path(self._temporary_directory.name) / "Outside"
        outside.mkdir()
        sentinel = outside / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        shutil.rmtree(workspace.compiler_output_directory)
        try:
            workspace.compiler_output_directory.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlink creation is unavailable: {error}")

        module = __import__(
            "farcel.infrastructure.project.modelica_asset_store", fromlist=["_remove_compiler_output"]
        )
        with self.assertRaises(EngineError) as raised:
            module._remove_compiler_output(workspace)

        self.assertEqual(ErrorCode.MODELICA_STAGING_ERROR, raised.exception.code)
        self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
