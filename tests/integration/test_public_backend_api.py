from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from farcel import FarcelEngine, create_backend
from farcel.contracts import (
    EngineError,
    ErrorCode,
    ExportReport,
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    SimulationResult,
    ValidationReport,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FMU_DIRECTORY = REPOSITORY_ROOT / "examples" / "fmus"


class PublicBackendApiTests(unittest.TestCase):
    def test_fmi2_complete_workflow_uses_public_api(self) -> None:
        metadata, result = self._run_public_workflow(FMU_DIRECTORY / "VanDerPol.fmu")

        self.assertEqual("2.0", metadata.fmi_version)
        self.assertEqual(InterfaceType.CO_SIMULATION, metadata.executable_interface)
        self.assertEqual(2, result.completed_steps)
        self.assertEqual(3, result.sample_count)
        self.assertEqual(("x0",), tuple(result.outputs))

    def test_fmi3_complete_workflow_uses_same_public_api(self) -> None:
        metadata, result = self._run_public_workflow(
            FMU_DIRECTORY / "VanDerPol-fmi3.fmu"
        )

        self.assertEqual("3.0", metadata.fmi_version)
        self.assertEqual(InterfaceType.CO_SIMULATION, metadata.executable_interface)
        self.assertEqual(2, result.completed_steps)
        self.assertEqual(3, result.sample_count)
        self.assertEqual(("x0",), tuple(result.outputs))

    def test_backend_example_imports_only_public_farcel_modules(self) -> None:
        example = REPOSITORY_ROOT / "examples" / "backend_api_example.py"
        tree = ast.parse(example.read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )

        farcel_modules = {name for name in imported_modules if name.startswith("farcel")}
        self.assertEqual({"farcel", "farcel.contracts"}, farcel_modules)
        self.assertNotIn("fmpy", imported_modules)

    def test_installed_consumer_runs_outside_repository(self) -> None:
        script = """
import sys
from pathlib import Path
from farcel import create_backend
from farcel.contracts import EngineError, ExportReport, ModelMetadata, SimulationConfig, SimulationResult, ValidationReport

backend = create_backend()
path = Path(sys.argv[1])
metadata = backend.load_fmu(path)
assert isinstance(metadata, ModelMetadata)
config = SimulationConfig(start_time=0.0, stop_time=0.02, communication_step=0.01, parameters={"mu": 2.0}, selected_outputs=("x0",))
validation = backend.validate_config(metadata, config)
assert isinstance(validation, ValidationReport)
result = backend.run_fmu(path, config)
assert isinstance(result, SimulationResult)
export = backend.export_result(result, Path(sys.argv[2]))
assert isinstance(export, ExportReport)
print("external consumer OK")
"""
        with tempfile.TemporaryDirectory() as directory:
            external_directory = Path(directory)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    script,
                    str((FMU_DIRECTORY / "VanDerPol.fmu").resolve()),
                    str(external_directory / "result.csv"),
                ],
                cwd=external_directory,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("external consumer OK", completed.stdout.strip())

    def test_public_errors_have_stable_fields(self) -> None:
        backend = create_backend()
        missing_path = REPOSITORY_ROOT / "does-not-exist.fmu"
        with self.assertRaises(EngineError) as missing:
            backend.load_fmu(missing_path)
        self.assertEqual(ErrorCode.IMPORT_ERROR, missing.exception.code)
        self.assertTrue(missing.exception.message)
        self.assertIsNotNone(missing.exception.details)

        with tempfile.TemporaryDirectory() as directory:
            invalid_path = Path(directory) / "invalid.fmu"
            invalid_path.write_bytes(b"not an FMU archive")
            with self.assertRaises(EngineError) as invalid:
                backend.load_fmu(invalid_path)
        self.assertEqual(ErrorCode.IMPORT_ERROR, invalid.exception.code)
        self.assertTrue(invalid.exception.message)

        metadata = backend.load_fmu(FMU_DIRECTORY / "VanDerPol.fmu")
        invalid_config = SimulationConfig(start_time=1.0, stop_time=0.0)
        with self.assertRaises(EngineError) as config:
            backend.validate_config(metadata, invalid_config)
        self.assertEqual(ErrorCode.CONFIG_ERROR, config.exception.code)
        self.assertTrue(config.exception.details["issues"])

        result = backend.run_fmu(
            FMU_DIRECTORY / "VanDerPol.fmu",
            SimulationConfig(start_time=0.0, stop_time=0.01),
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(EngineError) as export:
                backend.export_result(result, Path(directory))
        self.assertEqual(ErrorCode.EXPORT_ERROR, export.exception.code)
        self.assertTrue(export.exception.message)
        self.assertIn("destination", export.exception.details)

    def _run_public_workflow(self, fmu_path: Path):
        backend = create_backend()
        self.assertIsInstance(backend, FarcelEngine)
        metadata = backend.load_fmu(fmu_path)
        self.assertIsInstance(metadata, ModelMetadata)
        config = SimulationConfig(
            start_time=0.0,
            stop_time=0.02,
            communication_step=0.01,
            parameters={"mu": 2.0},
            selected_outputs=("x0",),
        )
        validation = backend.validate_config(metadata, config)
        self.assertIsInstance(validation, ValidationReport)
        self.assertTrue(validation.is_valid)
        result = backend.run_fmu(fmu_path, config)
        self.assertIsInstance(result, SimulationResult)

        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "result.csv"
            report = backend.export_result(result, destination)
            self.assertIsInstance(report, ExportReport)
            self.assertEqual(result.sample_count, report.row_count)
            self.assertTrue(destination.is_file())

        return metadata, result
