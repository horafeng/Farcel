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
            ),
        )

        self.assertTrue(summary.successful)
        self.assertEqual(summary.completed_steps, 5)
        self.assertAlmostEqual(summary.final_time, 0.05)
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
            ])
        self.assertEqual(exit_code, 0)
        self.assertIn("completed steps: 200", stdout.getvalue())
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
            ),
        )
        self.assertTrue(summary.successful)
        self.assertEqual(summary.completed_steps, 10)
        self.assertEqual(summary.final_time, 1.0)

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
