from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from farcel import create_backend
from farcel.contracts import (
    EngineError,
    ErrorCode,
    ModelicaCompileRequest,
    ModelicaCompileResult,
    ModelicaAssetStagingRequest,
    SimulationConfig,
    SimulationProject,
    SimulationState,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MODEL = REPOSITORY_ROOT / "examples" / "modelica" / "FarcelFirstOrder.mo"
FMU = REPOSITORY_ROOT / "examples" / "fmus" / "VanDerPol.fmu"
DEFAULT_OMC = Path(r"D:\OpenModelica\bin\omc.exe")


class ModelicaCompilePublicApiTests(unittest.TestCase):
    def test_default_backend_rejects_invalid_request_without_needing_omc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Source.mo"
            source.write_text("model Source end Source;", encoding="utf-8")
            output = root / "output"
            output.mkdir()
            request = ModelicaCompileRequest(source, "Bad); unsafe", output)

            with self.assertRaises(EngineError) as raised:
                create_backend().compile_modelica(request)

        self.assertEqual(ErrorCode.CONFIG_ERROR, raised.exception.code)
        self.assertEqual("model_name", raised.exception.details["issues"][0]["field"])

    def test_invalid_explicit_compiler_path_does_not_fall_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Source.mo"
            source.write_text("model Source end Source;", encoding="utf-8")
            output = root / "output"
            output.mkdir()
            request = ModelicaCompileRequest(
                source,
                "Source",
                output,
                compiler_path=root / "missing-omc.exe",
            )

            with self.assertRaises(EngineError) as raised:
                create_backend().compile_modelica(request)

        self.assertEqual(ErrorCode.MODELICA_COMPILER_NOT_FOUND, raised.exception.code)

    def test_default_backend_keeps_existing_fmu_workflow_without_compilation(self) -> None:
        backend = create_backend()

        metadata = backend.load_fmu(FMU)

        self.assertEqual("2.0", metadata.fmi_version)

    def test_project_schema_migration_is_explicit_and_schema_1_0_is_not_auto_compiled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Project"
            root.mkdir()
            backend = create_backend()
            legacy = SimulationProject("project", "旧项目")
            backend.save_project(root, legacy)
            request = ModelicaAssetStagingRequest(
                project_root=root,
                source_path=root / "missing.mo",
                model_name="FarcelFirstOrder",
            )

            with self.assertRaises(EngineError) as raised:
                backend.compile_project_modelica(legacy, request)
            self.assertEqual(ErrorCode.CONFIG_ERROR, raised.exception.code)
            self.assertEqual("1.0", backend.open_project(root).schema_version)

            migration = backend.migrate_project_schema(root)
            self.assertEqual("1.1", migration.project.schema_version)
            self.assertEqual(migration.project, backend.open_project(root))


@unittest.skipUnless(
    os.environ.get("FARCEL_RUN_OPENMODELICA_INTEGRATION") == "1",
    "需要显式设置 FARCEL_RUN_OPENMODELICA_INTEGRATION=1 并安装 OpenModelica",
)
class ModelicaCompilePublicApiRealIntegrationTests(unittest.TestCase):
    def test_compile_load_run_export_and_output_conflict(self) -> None:
        compiler_path = Path(os.environ.get("FARCEL_OPENMODELICA_OMC", DEFAULT_OMC))
        self.assertTrue(compiler_path.is_file(), f"未找到 OMC: {compiler_path}")

        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT / "artifacts") as directory:
            output = Path(directory) / "output"
            output.mkdir()
            request = ModelicaCompileRequest(
                MODEL,
                "FarcelFirstOrder",
                output,
                compiler_path=compiler_path,
            )
            backend = create_backend()

            compiled = backend.compile_modelica(request)
            self.assertIsInstance(compiled, ModelicaCompileResult)
            self.assertTrue(compiled.fmu_path.is_file())
            self.assertEqual(_sha256(compiled.fmu_path), compiled.sha256)

            metadata = backend.load_fmu(compiled.fmu_path)
            config = SimulationConfig(
                start_time=0.0,
                stop_time=2.0,
                communication_step=0.01,
                parameters={"T": 0.5},
                initial_inputs={"u": 1.0},
                selected_outputs=("y",),
            )
            backend.validate_config(metadata, config)
            result = backend.run_fmu(compiled.fmu_path, config)
            self.assertIs(SimulationState.COMPLETED, result.completion_state)
            maximum_error = max(
                abs(float(value) - (1.0 - math.exp(-time / 0.5)))
                for time, value in zip(result.timestamps, result.outputs["y"])
            )
            self.assertLessEqual(maximum_error, 0.03)
            exported = backend.export_result(result, Path(directory) / "result.csv")
            self.assertEqual(result.sample_count, exported.row_count)

            original_sha256 = _sha256(compiled.fmu_path)
            with self.assertRaises(EngineError) as raised:
                backend.compile_modelica(request)
            self.assertEqual(ErrorCode.MODELICA_OUTPUT_CONFLICT, raised.exception.code)
            self.assertTrue(compiled.fmu_path.is_file())
            self.assertEqual(original_sha256, _sha256(compiled.fmu_path))

    def test_project_compile_register_reopen_run_and_relocate(self) -> None:
        compiler_path = Path(os.environ.get("FARCEL_OPENMODELICA_OMC", DEFAULT_OMC))
        self.assertTrue(compiler_path.is_file(), f"未找到 OMC: {compiler_path}")

        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT / "artifacts") as directory:
            root = Path(directory) / "Project"
            root.mkdir()
            backend = create_backend()
            backend.save_project(root, SimulationProject("project", "真实 Modelica 项目"))
            migrated = backend.migrate_project_schema(root).project
            request = ModelicaAssetStagingRequest(
                project_root=root,
                source_path=MODEL,
                model_name="FarcelFirstOrder",
                compiler_path=compiler_path,
            )

            registration = backend.compile_project_modelica(migrated, request)
            updated = registration.project
            staged = registration.staged_build
            self.assertEqual(1, len(updated.model_assets))
            self.assertEqual((staged.provenance,), backend.list_project_modelica_builds(updated))
            self.assertEqual(staged.model_asset.asset_id, staged.provenance.fmu_asset_id)
            self.assertEqual(updated, backend.open_project(root))

            source = root / staged.provenance.source_snapshot.relative_path
            fmu = root / staged.model_asset.relative_path
            self.assertEqual(_sha256(source), staged.provenance.source_snapshot.sha256)
            self.assertEqual(_sha256(fmu), staged.model_asset.sha256)
            metadata = backend.load_fmu(fmu)
            config = SimulationConfig(
                start_time=0.0,
                stop_time=2.0,
                communication_step=0.01,
                parameters={"T": 0.5},
                initial_inputs={"u": 1.0},
                selected_outputs=("y",),
            )
            result = backend.run_fmu(fmu, config)
            self.assertIs(SimulationState.COMPLETED, result.completion_state)
            self.assertLessEqual(
                max(
                    abs(float(value) - (1.0 - math.exp(-time / 0.5)))
                    for time, value in zip(result.timestamps, result.outputs["y"])
                ),
                0.03,
            )
            relocated = Path(directory) / "RelocatedProject"
            shutil.copytree(root, relocated)
            self.assertTrue(backend.validate_project(relocated, backend.open_project(relocated)).is_valid)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    unittest.main()
