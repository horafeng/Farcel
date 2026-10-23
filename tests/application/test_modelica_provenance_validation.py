from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from farcel.application.modelica_provenance_validation import (
    require_valid_modelica_build_provenance,
    validate_modelica_build_provenance,
)
from farcel.contracts import (
    EngineError,
    ErrorCode,
    ModelAsset,
    ModelicaBuildProvenance,
    ModelicaSourceSnapshot,
)


_SHA256 = "a" * 64


class ModelicaProvenanceValidationTests(unittest.TestCase):
    def test_valid_provenance_accepts_a_unique_current_project_asset(self) -> None:
        asset = self._asset()

        report = validate_modelica_build_provenance(
            self._provenance(),
            model_assets=(asset,),
        )

        self.assertTrue(report.is_valid)

    def test_source_snapshot_path_rules_match_project_assets(self) -> None:
        for relative_path in (
            "C:/project/sources/FirstOrder.mo",
            "/project/sources/FirstOrder.mo",
            "../sources/FirstOrder.mo",
            "sources\\FirstOrder.mo",
        ):
            with self.subTest(relative_path=relative_path):
                report = validate_modelica_build_provenance(
                    replace(
                        self._provenance(),
                        source_snapshot=ModelicaSourceSnapshot(relative_path, _SHA256),
                    )
                )
                self.assertIn(
                    ("source_snapshot.relative_path", "PROJECT_PATH_INVALID"),
                    self._issue_keys(report),
                )

    def test_invalid_source_digest_is_rejected(self) -> None:
        report = validate_modelica_build_provenance(
            replace(
                self._provenance(),
                source_snapshot=ModelicaSourceSnapshot("sources/FirstOrder.mo", "ABC"),
            )
        )

        self.assertIn(
            ("source_snapshot.sha256", "SOURCE_SHA256_INVALID"),
            self._issue_keys(report),
        )

    def test_required_build_identity_and_compile_shape_rules_are_enforced(self) -> None:
        report = validate_modelica_build_provenance(
            replace(
                self._provenance(),
                build_id="",
                model_name="Example; quit()",
                compiler_name="",
                compiler_version="",
                fmi_version="3.0",
                fmu_type="me",
                fmu_asset_id="",
            )
        )

        self.assertEqual(
            {
                ("build_id", "EMPTY_BUILD_ID"),
                ("model_name", "INVALID_MODEL_NAME"),
                ("compiler_name", "EMPTY_COMPILER_NAME"),
                ("compiler_version", "EMPTY_COMPILER_VERSION"),
                ("fmi_version", "UNSUPPORTED_FMI_VERSION"),
                ("fmu_type", "UNSUPPORTED_FMU_TYPE"),
                ("fmu_asset_id", "EMPTY_FMU_ASSET_ID"),
            },
            set(self._issue_keys(report)),
        )

    def test_unknown_and_ambiguous_asset_references_are_reported(self) -> None:
        unknown = validate_modelica_build_provenance(
            self._provenance(),
            model_assets=(),
        )
        duplicate_asset = self._asset()
        ambiguous = validate_modelica_build_provenance(
            self._provenance(),
            model_assets=(self._asset(), duplicate_asset),
        )

        self.assertIn(("fmu_asset_id", "UNKNOWN_FMU_ASSET_ID"), self._issue_keys(unknown))
        self.assertIn(("fmu_asset_id", "AMBIGUOUS_FMU_ASSET_ID"), self._issue_keys(ambiguous))

    def test_validator_collects_multiple_issues_without_writing_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = validate_modelica_build_provenance(
                ModelicaBuildProvenance(
                    build_id="",
                    source_snapshot=ModelicaSourceSnapshot("../bad.mo", "invalid"),
                    model_name="bad name;",
                    compiler_name="",
                    compiler_version="",
                    fmi_version="1.0",
                    fmu_type="me",
                    fmu_asset_id="",
                )
            )

            self.assertGreaterEqual(len(report.issues), 8)
            self.assertEqual([], list(root.iterdir()))

    def test_required_validation_reuses_config_error(self) -> None:
        with self.assertRaises(EngineError) as raised:
            require_valid_modelica_build_provenance(
                replace(self._provenance(), model_name="bad name")
            )

        self.assertEqual(ErrorCode.CONFIG_ERROR, raised.exception.code)
        self.assertEqual("INVALID_MODEL_NAME", raised.exception.details["issues"][0]["code"])

    @staticmethod
    def _issue_keys(report) -> tuple[tuple[str, str], ...]:
        return tuple((issue.field, issue.code) for issue in report.issues)

    @staticmethod
    def _asset() -> ModelAsset:
        return ModelAsset(
            asset_id="asset-first-order",
            display_name="First order",
            relative_path="models/FirstOrder.fmu",
            sha256=_SHA256,
        )

    @staticmethod
    def _provenance() -> ModelicaBuildProvenance:
        return ModelicaBuildProvenance(
            build_id="build-001",
            source_snapshot=ModelicaSourceSnapshot("sources/FirstOrder.mo", _SHA256),
            model_name="ExampleSystems.FirstOrder",
            compiler_name="OpenModelica",
            compiler_version="1.26.3",
            fmi_version="2.0",
            fmu_type="cs",
            fmu_asset_id="asset-first-order",
        )
