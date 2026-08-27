from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from farcel import FarcelEngine, create_backend
from farcel.contracts import InterfaceType, SimulationConfig


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

    def _run_public_workflow(self, fmu_path: Path):
        backend = create_backend()
        self.assertIsInstance(backend, FarcelEngine)
        metadata = backend.load_fmu(fmu_path)
        config = SimulationConfig(
            start_time=0.0,
            stop_time=0.02,
            communication_step=0.01,
            parameters={"mu": 2.0},
            selected_outputs=("x0",),
        )
        self.assertTrue(backend.validate_config(metadata, config).is_valid)
        result = backend.run_fmu(fmu_path, config)

        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "result.csv"
            report = backend.export_result(result, destination)
            self.assertEqual(result.sample_count, report.row_count)
            self.assertTrue(destination.is_file())

        return metadata, result
