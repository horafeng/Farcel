"""Public-backend workflows that a PySide frontend may consume.

These tests deliberately exercise only Farcel's public API.  They do not
import PySide6, FMPy implementation modules, or require OpenModelica.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from farcel import create_backend
from farcel.contracts import (
    EngineError,
    ErrorCode,
    InputUpdate,
    ModelAsset,
    ModelicaBuildProvenance,
    ModelicaCompileRequest,
    ModelicaEnvironmentStatus,
    ModelicaSourceSnapshot,
    SimulationConfig,
    SimulationProject,
)


ROOT = Path(__file__).resolve().parents[2]
VAN_DER_POL = ROOT / "examples" / "fmus" / "VanDerPol.fmu"
FEEDTHROUGH = ROOT / "examples" / "fmus" / "Feedthrough-fmi2.fmu"


@unittest.skipUnless(
    VAN_DER_POL.is_file() and FEEDTHROUGH.is_file(), "repository FMU fixtures are unavailable"
)
class ModelicaFrontendPublicWorkflowTests(unittest.TestCase):
    def test_missing_omc_path_does_not_impair_existing_fmu_workflow(self) -> None:
        """An explicit bad compiler selection never falls back or blocks FMU use."""

        backend = create_backend()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            missing_omc = root / "missing-omc.exe"
            environment = backend.get_modelica_environment(missing_omc)
            self.assertIs(environment.status, ModelicaEnvironmentStatus.NOT_FOUND)
            self.assertFalse(environment.is_available)

            source = root / "Source.mo"
            source.write_text("model Source end Source;", encoding="utf-8")
            output = root / "output"
            output.mkdir()
            request = ModelicaCompileRequest(
                source, "Source", output, compiler_path=missing_omc
            )
            with self.assertRaises(EngineError) as missing:
                backend.compile_modelica(request)

        self.assertIs(missing.exception.code, ErrorCode.MODELICA_COMPILER_NOT_FOUND)

        metadata = backend.load_fmu(VAN_DER_POL)
        result = backend.run_fmu(
            VAN_DER_POL,
            SimulationConfig(stop_time=0.02, communication_step=0.01, selected_outputs=("x0",)),
        )
        self.assertEqual("2.0", metadata.fmi_version)
        self.assertEqual(3, len(result.timestamps))
        self.assertEqual(len(result.timestamps), len(result.outputs["x0"]))

    def test_metadata_driven_parameter_input_and_schedule_validation(self) -> None:
        """GUI controls build one SimulationConfig and use the public validator."""

        backend = create_backend()
        parameter_metadata = backend.load_fmu(VAN_DER_POL)
        input_metadata = backend.load_fmu(FEEDTHROUGH)

        parameter_config = SimulationConfig(
            stop_time=0.02,
            communication_step=0.01,
            parameters={"mu": 2.0},
            selected_outputs=("x0",),
        )
        input_config = SimulationConfig(
            stop_time=0.02,
            communication_step=0.01,
            initial_inputs={"Float64_continuous_input": 1.0},
            input_schedule=(
                InputUpdate(0.01, {"Float64_continuous_input": 2.0}),
            ),
            selected_outputs=("Float64_continuous_output",),
        )

        self.assertTrue(backend.validate_config(parameter_metadata, parameter_config).is_valid)
        self.assertTrue(backend.validate_config(input_metadata, input_config).is_valid)

        cases = (
            (
                parameter_metadata,
                SimulationConfig(parameters={"mu": "not-a-real"}),
                "parameters",
                "INVALID_PARAMETER_TYPE",
            ),
            (
                parameter_metadata,
                SimulationConfig(parameters={"unknown": 1.0}),
                "parameters",
                "UNKNOWN_PARAMETER",
            ),
            (
                input_metadata,
                SimulationConfig(parameters={"Float64_continuous_input": 1.0}),
                "parameters",
                "INVALID_PARAMETER_CAUSALITY",
            ),
            (
                parameter_metadata,
                SimulationConfig(initial_inputs={"mu": 1.0}),
                "initial_inputs",
                "INVALID_INPUT_CAUSALITY",
            ),
            (
                input_metadata,
                SimulationConfig(
                    stop_time=0.02,
                    communication_step=0.01,
                    input_schedule=(
                        InputUpdate(0.015, {"Float64_continuous_input": 2.0}),
                    ),
                ),
                "input_schedule[0]",
                "INPUT_TIME_NOT_COMMUNICATION_POINT",
            ),
        )
        for metadata, config, field, issue_code in cases:
            with self.subTest(field=field, issue_code=issue_code):
                with self.assertRaises(EngineError) as invalid:
                    backend.validate_config(metadata, config)
                self.assertIs(invalid.exception.code, ErrorCode.CONFIG_ERROR)
                issues = invalid.exception.details["issues"]
                self.assertIn(
                    {"field": field, "code": issue_code},
                    tuple({"field": issue["field"], "code": issue["code"]} for issue in issues),
                )

    def test_project_schema_migration_is_explicit_and_keeps_legacy_data(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "LegacyProject"
            root.mkdir()
            legacy_asset = _copy_van_der_pol_asset(root)
            legacy = SimulationProject("legacy", "旧 Project", (legacy_asset,))
            backend = create_backend()
            backend.save_project(root, legacy)
            original_document = (root / "project.json").read_bytes()

            opened = backend.open_project(root)
            self.assertEqual("1.0", opened.schema_version)
            self.assertEqual(legacy, opened)
            self.assertEqual(original_document, (root / "project.json").read_bytes())

            migration = backend.migrate_project_schema(root)
            self.assertEqual("1.1", migration.project.schema_version)
            self.assertEqual(legacy.model_assets, migration.project.model_assets)
            self.assertEqual(original_document, (root / migration.backup_relative_path).read_bytes())
            self.assertEqual(migration.project, backend.open_project(root))

    def test_project_provenance_display_is_read_only(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "ProvenanceProject"
            root.mkdir()
            asset, provenance = _provenance_fixture(root)
            project = SimulationProject(
                "provenance-project",
                "来源展示",
                (asset,),
                schema_version="1.1",
                modelica_build_provenance=(provenance,),
            )
            backend = create_backend()
            backend.save_project(root, project)
            before = (root / "project.json").read_bytes()

            opened = backend.open_project(root)
            builds = backend.list_project_modelica_builds(opened)

            self.assertEqual((provenance,), builds)
            self.assertEqual(asset.asset_id, builds[0].fmu_asset_id)
            self.assertIn(
                builds[0].fmu_asset_id,
                {model_asset.asset_id for model_asset in opened.model_assets},
            )
            self.assertEqual(before, (root / "project.json").read_bytes())
            self.assertEqual(opened, backend.open_project(root))

    def test_gui_can_rely_on_stable_public_error_payloads(self) -> None:
        """Public calls expose codes, messages and structured details, not tracebacks."""

        backend = create_backend()
        metadata = backend.load_fmu(VAN_DER_POL)
        with self.assertRaises(EngineError) as invalid_config:
            backend.validate_config(metadata, SimulationConfig(parameters={"mu": "bad"}))

        with TemporaryDirectory() as directory:
            missing_root = Path(directory) / "missing-project"
            with self.assertRaises(EngineError) as missing_project:
                backend.open_project(missing_root)

            malformed_root = Path(directory) / "malformed-project"
            malformed_root.mkdir()
            (malformed_root / "project.json").write_text("{", encoding="utf-8")
            with self.assertRaises(EngineError) as malformed_project:
                backend.open_project(malformed_root)

        for captured, expected in (
            (invalid_config.exception, ErrorCode.CONFIG_ERROR),
            (missing_project.exception, ErrorCode.PROJECT_IO_ERROR),
            (malformed_project.exception, ErrorCode.PROJECT_FORMAT_ERROR),
        ):
            with self.subTest(code=expected.value):
                self.assertIs(captured.code, expected)
                self.assertTrue(captured.message)
                self.assertIsInstance(captured.details, dict)


def _copy_van_der_pol_asset(root: Path) -> ModelAsset:
    destination = root / "models" / VAN_DER_POL.name
    destination.parent.mkdir()
    shutil.copyfile(VAN_DER_POL, destination)
    return ModelAsset(
        "vdp",
        "Van der Pol",
        "models/VanDerPol.fmu",
        _sha256(destination),
    )


def _provenance_fixture(root: Path) -> tuple[ModelAsset, ModelicaBuildProvenance]:
    build_id = "a" * 32
    build_root = root / "modelica-builds" / f"build-{build_id}"
    source = build_root / "source" / "model.mo"
    fmu = build_root / "model" / "model.fmu"
    source.parent.mkdir(parents=True)
    fmu.parent.mkdir(parents=True)
    source.write_text("model FirstOrder end FirstOrder;", encoding="utf-8")
    fmu.write_bytes(b"verified-fmu-placeholder")
    asset = ModelAsset(
        "modelica-" + build_id,
        "一阶系统",
        fmu.relative_to(root).as_posix(),
        _sha256(fmu),
    )
    provenance = ModelicaBuildProvenance(
        build_id,
        ModelicaSourceSnapshot(source.relative_to(root).as_posix(), _sha256(source)),
        "Example.FirstOrder",
        "OpenModelica",
        "1.26.3",
        "2.0",
        "cs",
        asset.asset_id,
    )
    return asset, provenance


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
