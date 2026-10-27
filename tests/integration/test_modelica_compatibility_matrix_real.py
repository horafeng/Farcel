"""Opt-in real OpenModelica compatibility coverage through Farcel's public API."""

from __future__ import annotations

from contextlib import contextmanager
import csv
import hashlib
import math
import os
from pathlib import Path
import re
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
import zipfile

from farcel import create_backend
from farcel.contracts import ModelicaCompileRequest, SimulationConfig, SimulationState


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MODELS = REPOSITORY_ROOT / "examples" / "modelica"
SECOND_ORDER = MODELS / "FarcelSecondOrder.mo"
ALGEBRAIC_GAIN = MODELS / "FarcelAlgebraicGain.mo"
DEFAULT_OMC = Path(r"D:\OpenModelica\bin\omc.exe")
MODEL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@unittest.skipUnless(
    os.environ.get("FARCEL_RUN_OPENMODELICA_INTEGRATION") == "1",
    "需要显式设置 FARCEL_RUN_OPENMODELICA_INTEGRATION=1 并安装 OpenModelica",
)
class ModelicaCompatibilityMatrixRealIntegrationTests(unittest.TestCase):
    """Compile, inspect, run and export representative real Modelica models."""

    def test_second_order_dynamic_model_compiles_runs_and_exports(self) -> None:
        with self._temporary_root() as root:
            backend = create_backend()
            self._assert_omc_environment(backend)
            compiled = self._compile(backend, root, SECOND_ORDER, "FarcelSecondOrder")
            _assert_fmu_structure(self, compiled.fmu_path, compiled.sha256)
            metadata = backend.load_fmu(compiled.fmu_path)
            _assert_variable_causalities(
                self,
                metadata,
                {"T": "parameter", "u": "input", "y": "output"},
            )

            fast_config = _config(T=0.5, u=1.0)
            self.assertTrue(backend.validate_config(metadata, fast_config).is_valid)
            fast = backend.run_fmu(compiled.fmu_path, fast_config)
            _assert_complete_series(self, fast)
            fast_error = _second_order_maximum_error(fast, T=0.5, u=1.0)
            self.assertLessEqual(fast_error, 0.03)

            slow = backend.run_fmu(compiled.fmu_path, _config(T=1.0, u=1.0))
            _assert_complete_series(self, slow)
            slow_error = _second_order_maximum_error(slow, T=1.0, u=1.0)
            self.assertLessEqual(slow_error, 0.03)
            self.assertGreater(float(fast.outputs["y"][50]), float(slow.outputs["y"][50]))
            self.assertEqual(compiled.sha256, _sha256(compiled.fmu_path))

            csv_path = root / "second-order.csv"
            exported = backend.export_result(fast, csv_path)
            self.assertEqual(fast.sample_count, exported.row_count)
            _assert_csv_matches_result(self, csv_path, fast)

    def test_algebraic_gain_model_compiles_loads_and_runs(self) -> None:
        with self._temporary_root() as root:
            backend = create_backend()
            self._assert_omc_environment(backend)
            compiled = self._compile(backend, root, ALGEBRAIC_GAIN, "FarcelAlgebraicGain")
            _assert_fmu_structure(self, compiled.fmu_path, compiled.sha256)
            metadata = backend.load_fmu(compiled.fmu_path)
            _assert_variable_causalities(
                self,
                metadata,
                {"K": "parameter", "u": "input", "y": "output"},
            )

            first_config = _config(K=2.0, u=1.5)
            self.assertTrue(backend.validate_config(metadata, first_config).is_valid)
            first = backend.run_fmu(compiled.fmu_path, first_config)
            _assert_complete_series(self, first)
            _assert_constant_output(self, first, expected=3.0)

            second = backend.run_fmu(compiled.fmu_path, _config(K=3.0, u=2.0))
            _assert_complete_series(self, second)
            _assert_constant_output(self, second, expected=6.0)
            self.assertEqual(compiled.sha256, _sha256(compiled.fmu_path))

    @staticmethod
    @contextmanager
    def _temporary_root():
        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT / "artifacts") as directory:
            yield Path(directory)

    def _assert_omc_environment(self, backend) -> None:
        compiler_path = _omc_path()
        self.assertTrue(compiler_path.is_file(), f"未找到 OMC: {compiler_path}")
        environment = backend.get_modelica_environment(compiler_path)
        self.assertTrue(environment.is_available, environment.diagnostic_message)
        self.assertEqual("OpenModelica", environment.compiler_name)
        self.assertEqual("1.26.3", environment.compiler_version)

    def _compile(self, backend, root: Path, source: Path, model_name: str):
        output = root / model_name
        output.mkdir()
        compiled = backend.compile_modelica(
            ModelicaCompileRequest(
                source_path=source,
                model_name=model_name,
                output_directory=output,
                compiler_path=_omc_path(),
            )
        )
        self.assertTrue(Path(compiled.fmu_path).is_file())
        self.assertEqual("2.0", compiled.fmi_version)
        self.assertEqual("cs", compiled.fmu_type)
        self.assertEqual(_sha256(compiled.fmu_path), compiled.sha256)
        return compiled


def _config(*, T: float | None = None, K: float | None = None, u: float) -> SimulationConfig:
    parameters = {}
    if T is not None:
        parameters["T"] = T
    if K is not None:
        parameters["K"] = K
    return SimulationConfig(
        start_time=0.0,
        stop_time=2.0,
        communication_step=0.01,
        parameters=parameters,
        initial_inputs={"u": u},
        selected_outputs=("y",),
    )


def _assert_fmu_structure(test: unittest.TestCase, fmu_path: str | Path, expected_sha256: str) -> None:
    path = Path(fmu_path)
    test.assertTrue(zipfile.is_zipfile(path))
    test.assertEqual(expected_sha256, _sha256(path))
    with zipfile.ZipFile(path) as archive:
        test.assertIn("modelDescription.xml", archive.namelist())
        root = ElementTree.fromstring(archive.read("modelDescription.xml"))
        test.assertEqual("2.0", root.attrib.get("fmiVersion"))
        co_simulation = root.find("{*}CoSimulation")
        test.assertIsNotNone(co_simulation)
        assert co_simulation is not None
        model_identifier = co_simulation.attrib.get("modelIdentifier", "")
        test.assertIsNotNone(MODEL_IDENTIFIER.fullmatch(model_identifier))
        test.assertIn(
            f"binaries/win64/{model_identifier}.dll",
            archive.namelist(),
        )


def _assert_variable_causalities(test: unittest.TestCase, metadata, expected: dict[str, str]) -> None:
    actual = {variable.name: variable.causality for variable in metadata.variables}
    for name, causality in expected.items():
        with test.subTest(variable=name):
            test.assertEqual(causality, actual.get(name))


def _assert_complete_series(test: unittest.TestCase, result) -> None:
    test.assertIs(SimulationState.COMPLETED, result.completion_state)
    test.assertAlmostEqual(0.0, result.start_time, places=12)
    test.assertAlmostEqual(2.0, result.final_time, places=12)
    test.assertAlmostEqual(0.0, result.timestamps[0], places=12)
    test.assertAlmostEqual(2.0, result.timestamps[-1], places=12)
    test.assertEqual(201, result.sample_count)
    test.assertEqual(201, len(result.outputs["y"]))
    test.assertTrue(all(math.isfinite(time) for time in result.timestamps))
    test.assertTrue(all(math.isfinite(float(value)) for value in result.outputs["y"]))
    test.assertTrue(all(left < right for left, right in zip(result.timestamps, result.timestamps[1:])))


def _second_order_maximum_error(result, *, T: float, u: float) -> float:
    return max(
        abs(float(value) - u * (1.0 - (1.0 + time / T) * math.exp(-time / T)))
        for time, value in zip(result.timestamps, result.outputs["y"])
    )


def _assert_constant_output(test: unittest.TestCase, result, *, expected: float) -> None:
    test.assertTrue(
        all(math.isclose(float(value), expected, rel_tol=0.0, abs_tol=1e-10) for value in result.outputs["y"])
    )


def _assert_csv_matches_result(test: unittest.TestCase, path: Path, result) -> None:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.reader(stream))
    test.assertEqual(["time", "y"], rows[0])
    test.assertEqual(result.sample_count, len(rows) - 1)
    for index, row in enumerate(rows[1:]):
        test.assertEqual(2, len(row))
        test.assertAlmostEqual(result.timestamps[index], float(row[0]), places=12)
        test.assertAlmostEqual(float(result.outputs["y"][index]), float(row[1]), places=12)


def _omc_path() -> Path:
    return Path(os.environ.get("FARCEL_OPENMODELICA_OMC", DEFAULT_OMC))


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    unittest.main()
