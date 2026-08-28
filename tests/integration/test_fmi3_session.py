import csv
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from farcel.application.engine import FarcelEngine
from farcel.cli import main
from farcel.contracts.models import InterfaceType, SimulationConfig
from farcel.infrastructure.fmpy import FmpyImporter, FmpySessionFactory
from farcel.infrastructure.fmpy.fmi3_session import FmpyFmi3Session


FMU_FIXTURES = Path(__file__).resolve().parents[2] / "examples" / "fmus"


class CapturingFactory:
    def __init__(self) -> None:
        self.inner = FmpySessionFactory()
        self.session = None
        self.extraction_directory: Path | None = None

    def create(self, metadata, config):
        self.session = self.inner.create(metadata, config)
        self.extraction_directory = self.session._extraction_directory
        return self.session


class Fmi3SessionIntegrationTests(unittest.TestCase):
    van_der_pol = FMU_FIXTURES / "VanDerPol-fmi3.fmu"

    @unittest.skipUnless(van_der_pol.is_file(), "FMI 3 VanDerPol is unavailable")
    def test_metadata_and_executable_policy_for_real_fmi3_fmu(self) -> None:
        metadata = FmpyImporter().load(self.van_der_pol)

        self.assertEqual(metadata.fmi_version, "3.0")
        self.assertIn(InterfaceType.CO_SIMULATION, metadata.interface_types)
        self.assertEqual(metadata.executable_interface, InterfaceType.CO_SIMULATION)
        self.assertTrue(metadata.capabilities.can_execute)
        self.assertGreater(len(metadata.variables), 0)

    @unittest.skipUnless(van_der_pol.is_file(), "FMI 3 VanDerPol is unavailable")
    def test_existing_cli_inspects_real_fmi3_fmu(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = main(["inspect", str(self.van_der_pol)])

        self.assertEqual(exit_code, 0)
        self.assertIn("FMI 版本: 3.0", stdout.getvalue())
        self.assertIn("接口类型: co_simulation, model_exchange", stdout.getvalue())
        self.assertIn("Farcel 当前可执行: 是", stdout.getvalue())
        self.assertIn("变量数量: 6", stdout.getvalue())

    @unittest.skipUnless(van_der_pol.is_file(), "FMI 3 VanDerPol is unavailable")
    def test_real_fmi3_run_produces_canonical_result_and_cleans_up(self) -> None:
        factory = CapturingFactory()
        result = FarcelEngine(FmpyImporter(), factory).run_fmu(
            self.van_der_pol,
            SimulationConfig(
                start_time=0.0,
                stop_time=0.05,
                communication_step=0.01,
                parameters={"mu": 2.0},
                selected_outputs=("x0",),
            ),
        )

        self.assertIsInstance(factory.session, FmpyFmi3Session)
        self.assertTrue(result.successful)
        self.assertEqual(result.completed_steps, 5)
        self.assertEqual(result.sample_count, 6)
        self.assertEqual(result.timestamps[0], 0.0)
        self.assertAlmostEqual(result.timestamps[-1], 0.05)
        self.assertTrue(
            all(
                left < right
                for left, right in zip(result.timestamps, result.timestamps[1:])
            )
        )
        self.assertEqual(tuple(result.outputs), ("x0",))
        self.assertEqual(result.outputs["x0"][0], 2.0)
        self.assertNotEqual(result.outputs["x0"][-1], result.outputs["x0"][0])
        self.assertTrue(factory.session._terminated)
        self.assertTrue(factory.session._closed)
        self.assertFalse(factory.extraction_directory.exists())

    @unittest.skipUnless(van_der_pol.is_file(), "FMI 3 VanDerPol is unavailable")
    def test_real_fmi3_uses_same_output_interval_semantics(self) -> None:
        result = FarcelEngine(
            FmpyImporter(), FmpySessionFactory()
        ).run_fmu(
            self.van_der_pol,
            SimulationConfig(
                start_time=0.0,
                stop_time=0.2,
                communication_step=0.01,
                output_interval=0.05,
                selected_outputs=("x0",),
            ),
        )

        self.assertTrue(result.successful)
        self.assertEqual(result.completed_steps, 20)
        self.assertEqual(result.sample_count, 5)
        for actual, expected in zip(result.timestamps, (0.0, 0.05, 0.1, 0.15, 0.2)):
            self.assertAlmostEqual(actual, expected)

    @unittest.skipUnless(van_der_pol.is_file(), "FMI 3 VanDerPol is unavailable")
    def test_real_fmi3_parameter_override_reaches_instance(self) -> None:
        factory = CapturingFactory()
        engine = FarcelEngine(FmpyImporter(), factory)
        metadata = engine.load_fmu(self.van_der_pol)
        config = SimulationConfig(parameters={"mu": 2.0})
        handle = engine.create_session(metadata.model_id, config)
        engine.initialize(handle)
        try:
            mu = next(variable for variable in metadata.variables if variable.name == "mu")
            value = factory.session._fmu.getFloat64([mu.value_reference])[0]
            self.assertEqual(value, 2.0)
        finally:
            engine.terminate(handle)
            engine.close_session(handle)
        self.assertFalse(factory.extraction_directory.exists())

    @unittest.skipUnless(van_der_pol.is_file(), "FMI 3 VanDerPol is unavailable")
    def test_real_fmi3_run_without_outputs_keeps_timeline(self) -> None:
        result = FarcelEngine(
            FmpyImporter(), FmpySessionFactory()
        ).run_fmu(
            self.van_der_pol,
            SimulationConfig(
                start_time=0.0,
                stop_time=0.02,
                communication_step=0.01,
            ),
        )

        self.assertTrue(result.successful)
        self.assertEqual(result.completed_steps, 2)
        self.assertEqual(result.sample_count, 3)
        self.assertEqual(result.outputs, {})

    @unittest.skipUnless(van_der_pol.is_file(), "FMI 3 VanDerPol is unavailable")
    def test_existing_cli_export_writes_real_fmi3_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "VanDerPol-fmi3.csv"
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main([
                    "export", str(self.van_der_pol), "--start-time", "0",
                    "--stop-time", "0.02", "--step-size", "0.01",
                    "--parameter", "mu=2.0", "--output", "x0",
                    "--csv", str(destination),
                ])
            with destination.open("r", encoding="utf-8", newline="") as stream:
                rows = list(csv.reader(stream))

        self.assertEqual(exit_code, 0)
        self.assertIn("data rows: 3", stdout.getvalue())
        self.assertEqual(rows[0], ["time", "x0"])
        self.assertEqual(len(rows), 4)
        self.assertEqual(float(rows[1][0]), 0.0)
        self.assertAlmostEqual(float(rows[-1][0]), 0.02)
        self.assertEqual(float(rows[1][1]), 2.0)
        self.assertNotEqual(float(rows[-1][1]), float(rows[1][1]))


if __name__ == "__main__":
    unittest.main()
