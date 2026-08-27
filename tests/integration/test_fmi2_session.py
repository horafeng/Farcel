import csv
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from farcel.application.engine import FarcelEngine
from farcel.cli import main
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import SimulationConfig
from farcel.infrastructure.fmpy import FmpyFmi2SessionFactory, FmpyImporter


FMU_FIXTURES = Path(__file__).resolve().parents[2] / "examples" / "fmus"


class CapturingFactory:
    def __init__(self) -> None:
        self.inner = FmpyFmi2SessionFactory()
        self.session = None
        self.extraction_directory: Path | None = None

    def create(self, metadata, config):
        self.session = self.inner.create(metadata, config)
        self.extraction_directory = self.session._extraction_directory
        return self.session


class Fmi2SessionIntegrationTests(unittest.TestCase):
    van_der_pol = FMU_FIXTURES / "VanDerPol.fmu"
    manipulator = FMU_FIXTURES / "manipulator.fmu"
    stair = FMU_FIXTURES / "Stair.fmu"

    @unittest.skipUnless(van_der_pol.is_file(), "VanDerPol FMU is unavailable")
    def test_real_fmu_runs_multiple_steps_and_releases_resources(self) -> None:
        factory = CapturingFactory()
        engine = FarcelEngine(FmpyImporter(), factory)
        summary = engine.run_fmu(
            self.van_der_pol,
            SimulationConfig(
                start_time=0.0,
                stop_time=0.05,
                communication_step=0.01,
                parameters={"mu": 2.0},
                selected_outputs=("x0",),
            ),
        )

        self.assertTrue(summary.successful)
        self.assertEqual(summary.completed_steps, 5)
        self.assertAlmostEqual(summary.final_time, 0.05)
        self.assertEqual(summary.sample_count, 6)
        self.assertEqual(tuple(summary.outputs), ("x0",))
        self.assertEqual(len(summary.outputs["x0"]), summary.sample_count)
        self.assertIsNotNone(factory.session)
        self.assertTrue(factory.session._terminated)
        self.assertTrue(factory.session._closed)
        self.assertFalse(factory.extraction_directory.exists())

    @unittest.skipUnless(van_der_pol.is_file(), "VanDerPol FMU is unavailable")
    def test_parameter_override_reaches_real_fmu_instance(self) -> None:
        factory = CapturingFactory()
        engine = FarcelEngine(FmpyImporter(), factory)
        metadata = engine.load_fmu(self.van_der_pol)
        config = SimulationConfig(parameters={"mu": 2.0})
        handle = engine.create_session(metadata.model_id, config)
        engine.initialize(handle)
        try:
            mu = next(variable for variable in metadata.variables if variable.name == "mu")
            value = factory.session._fmu.getReal([mu.value_reference])[0]
            self.assertEqual(value, 2.0)
        finally:
            engine.terminate(handle)
            engine.close_session(handle)
        self.assertFalse(factory.extraction_directory.exists())

    @unittest.skipUnless(van_der_pol.is_file(), "VanDerPol FMU is unavailable")
    def test_run_cli_prints_expected_summary(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = main([
                "run", str(self.van_der_pol), "--start-time", "0",
                "--stop-time", "2", "--step-size", "0.01",
                "--parameter", "mu=2.0",
                "--output", "x0",
            ])
        self.assertEqual(exit_code, 0)
        self.assertIn("completed steps: 200", stdout.getvalue())
        self.assertIn("samples: 201", stdout.getvalue())
        self.assertIn("selected outputs: x0", stdout.getvalue())
        self.assertIn("final simulation time: 2.0", stdout.getvalue())
        self.assertIn("execution successful: yes", stdout.getvalue())

    @unittest.skipUnless(stair.is_file(), "Stair FMU is unavailable")
    def test_second_real_fmi2_fmu_smoke_test(self) -> None:
        summary = FarcelEngine(
            FmpyImporter(), FmpyFmi2SessionFactory()
        ).run_fmu(
            self.stair,
            SimulationConfig(
                start_time=0.0,
                stop_time=1.0,
                communication_step=0.1,
                selected_outputs=("counter",),
            ),
        )
        self.assertTrue(summary.successful)
        self.assertEqual(summary.completed_steps, 10)
        self.assertAlmostEqual(summary.final_time, 1.0)
        self.assertEqual(summary.sample_count, 11)
        self.assertEqual(tuple(summary.outputs), ("counter",))
        self.assertEqual(summary.outputs["counter"][0], 1)
        self.assertEqual(len(summary.outputs["counter"]), 11)

    @unittest.skipUnless(van_der_pol.is_file(), "VanDerPol FMU is unavailable")
    def test_real_result_uses_actual_times_and_only_selected_outputs(self) -> None:
        result = FarcelEngine(
            FmpyImporter(), FmpyFmi2SessionFactory()
        ).run_fmu(
            self.van_der_pol,
            SimulationConfig(
                start_time=0.0,
                stop_time=2.0,
                communication_step=0.01,
                parameters={"mu": 2.0},
                selected_outputs=("x0",),
            ),
        )

        self.assertEqual(result.completed_steps, 200)
        self.assertEqual(result.sample_count, 201)
        self.assertEqual(result.timestamps[0], 0.0)
        self.assertAlmostEqual(result.timestamps[-1], 2.0)
        self.assertTrue(
            all(left < right for left, right in zip(result.timestamps, result.timestamps[1:]))
        )
        self.assertEqual(tuple(result.outputs), ("x0",))
        self.assertEqual(len(result.outputs["x0"]), result.sample_count)
        self.assertEqual(result.outputs["x0"][0], 2.0)
        self.assertNotEqual(result.outputs["x0"][-1], result.outputs["x0"][0])

    @unittest.skipUnless(van_der_pol.is_file(), "VanDerPol FMU is unavailable")
    def test_real_run_without_outputs_keeps_timeline(self) -> None:
        result = FarcelEngine(
            FmpyImporter(), FmpyFmi2SessionFactory()
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

    @unittest.skipUnless(van_der_pol.is_file(), "VanDerPol FMU is unavailable")
    def test_cli_exports_real_simulation_result_to_csv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "VanDerPol.csv"
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main([
                    "export", str(self.van_der_pol), "--start-time", "0",
                    "--stop-time", "0.02", "--step-size", "0.01",
                    "--output", "x0", "--csv", str(destination),
                ])

            with destination.open("r", encoding="utf-8", newline="") as stream:
                rows = list(csv.reader(stream))

        self.assertEqual(exit_code, 0)
        self.assertIn("data rows: 3", stdout.getvalue())
        self.assertIn("export successful", stdout.getvalue())
        self.assertEqual(rows[0], ["time", "x0"])
        self.assertEqual(len(rows), 4)
        self.assertEqual(float(rows[1][0]), 0.0)
        self.assertAlmostEqual(float(rows[-1][0]), 0.02)
        self.assertEqual(float(rows[1][1]), 2.0)
        self.assertNotEqual(float(rows[-1][1]), float(rows[1][1]))

    @unittest.skipUnless(manipulator.is_file(), "manipulator FMU is unavailable")
    def test_real_step_failure_is_stable_and_releases_resources(self) -> None:
        factory = CapturingFactory()
        engine = FarcelEngine(FmpyImporter(), factory)
        with self.assertRaises(EngineError) as raised:
            engine.run_fmu(
                self.manipulator,
                SimulationConfig(
                    start_time=0.0,
                    stop_time=0.03,
                    communication_step=0.01,
                ),
            )
        self.assertEqual(raised.exception.code, ErrorCode.STEP_ERROR)
        self.assertTrue(factory.session._closed)
        self.assertFalse(factory.extraction_directory.exists())
