import csv
import math
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from farcel.application.engine import FarcelEngine
from farcel.cli import main
from farcel.contracts import RunControl
from farcel.contracts.models import (
    InputUpdate,
    InterfaceType,
    SimulationConfig,
    SimulationState,
)
from farcel.infrastructure.export.csv import CsvResultExporter
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
    bouncing_ball = FMU_FIXTURES / "BouncingBall-fmi3.fmu"
    state_space = FMU_FIXTURES / "StateSpace-fmi3.fmu"
    patched_state_space = FMU_FIXTURES / "StateSpace-fmi3-patched.fmu"

    @unittest.skipUnless(state_space.is_file(), "FMI 3 StateSpace is unavailable")
    def test_statespace_metadata_exposes_default_resolved_array_dimensions(self) -> None:
        metadata = FmpyImporter().load(self.state_space)
        variables = {variable.name: variable for variable in metadata.variables}

        self.assertEqual(metadata.fmi_version, "3.0")
        self.assertEqual(metadata.executable_interface, InterfaceType.CO_SIMULATION)
        self.assertTrue(metadata.capabilities.can_execute)
        self.assertEqual(
            {name: variables[name].shape for name in ("A", "B", "C", "D")},
            {"A": (3, 3), "B": (3, 3), "C": (3, 3), "D": (3, 3)},
        )
        self.assertEqual(
            {name: variables[name].shape for name in ("x0", "u", "y")},
            {"x0": (3,), "u": (3,), "y": (3,)},
        )
        self.assertEqual(
            {
                name: variables[name].dimension_value_references
                for name in ("A", "B", "C", "D", "x0", "u", "y")
            },
            {
                "A": (2, 2),
                "B": (2, 1),
                "C": (3, 2),
                "D": (3, 1),
                "x0": (2,),
                "u": (1,),
                "y": (3,),
            },
        )
        self.assertEqual(variables["A"].start[0], (1.0, 0.0, 0.0))
        self.assertEqual(variables["u"].start, (1.0, 2.0, 3.0))
        self.assertIsNone(variables["y"].start)
        self.assertEqual(
            {name: variables[name].causality for name in ("m", "n", "r")},
            {
                "m": "structuralParameter",
                "n": "structuralParameter",
                "r": "structuralParameter",
            },
        )

    @unittest.skipUnless(state_space.is_file(), "FMI 3 StateSpace is unavailable")
    def test_statespace_default_run_streams_array_result_and_exports_indexed_csv(self) -> None:
        chunks = []
        result = FarcelEngine(FmpyImporter(), FmpySessionFactory()).run_fmu(
            self.state_space,
            SimulationConfig(
                start_time=0.0,
                stop_time=2.0,
                communication_step=1.0,
                output_interval=1.0,
                selected_outputs=("y",),
            ),
            on_result_chunk=chunks.append,
            result_chunk_size=2,
        )

        self.assertEqual(result.completion_state, SimulationState.COMPLETED)
        self.assertEqual(result.timestamps, (0.0, 1.0, 2.0))
        self.assertTrue(
            all(
                isinstance(sample, tuple)
                and len(sample) == 3
                and all(math.isfinite(value) for value in sample)
                for sample in result.outputs["y"]
            )
        )
        self.assertEqual(
            tuple(sample for chunk in chunks for sample in chunk.columns["y"]),
            result.outputs["y"],
        )
        self.assertEqual([chunk.final_chunk for chunk in chunks], [False, True])

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "StateSpace.csv"
            report = CsvResultExporter().export(result, destination)
            with destination.open("r", encoding="utf-8", newline="") as stream:
                rows = list(csv.reader(stream))

        self.assertEqual(report.row_count, result.sample_count)
        self.assertEqual(rows[0], ["time", "y[0]", "y[1]", "y[2]"])
        self.assertEqual(len(rows), result.sample_count + 1)
        self.assertEqual(tuple(float(value) for value in rows[1][1:]), (1.0, 2.0, 3.0))

    @unittest.skipUnless(state_space.is_file(), "FMI 3 StateSpace is unavailable")
    def test_statespace_resolved_array_parameters_and_inputs_run_without_configuration_mode(self) -> None:
        engine = FarcelEngine(FmpyImporter(), FmpySessionFactory())
        result = engine.run_fmu(
            self.state_space,
            SimulationConfig(
                start_time=0.0,
                stop_time=2.0,
                communication_step=1.0,
                output_interval=1.0,
                parameters={
                    "A": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
                    "x0": (0.0, 0.0, 0.0),
                },
                initial_inputs={"u": (4.0, 5.0, 6.0)},
                input_schedule=(
                    InputUpdate(1.0, {"u": (1.0, 2.0, 3.0)}),
                ),
                selected_outputs=("y",),
            ),
        )

        self.assertTrue(result.successful)
        self.assertEqual(result.outputs["y"][0], (4.0, 5.0, 6.0))
        self.assertNotEqual(result.outputs["y"][-1], result.outputs["y"][0])

    @unittest.skipUnless(
        patched_state_space.is_file(), "patched FMI 3 StateSpace is unavailable"
    )
    def test_patched_statespace_runs_dynamic_shapes_streams_chunks_exports_csv_and_cleans_up(self) -> None:
        factory = CapturingFactory()
        engine = FarcelEngine(FmpyImporter(), factory)
        metadata = engine.load_fmu(self.patched_state_space)
        chunks = []
        variables = {variable.name: variable for variable in metadata.variables}
        zeros = lambda rows, columns: tuple(
            tuple(0.0 for _ in range(columns)) for _ in range(rows)
        )

        self.assertEqual(variables["A"].shape, (3, 3))
        self.assertEqual(variables["y"].shape, (3,))
        result = engine.run_fmu(
            self.patched_state_space,
            SimulationConfig(
                start_time=0.0,
                stop_time=2.0,
                communication_step=1.0,
                output_interval=1.0,
                parameters={
                    "m": 2,
                    "n": 4,
                    "r": 1,
                    "A": zeros(4, 4),
                    "B": ((1.0, 0.0), (0.0, 1.0), (0.0, 0.0), (0.0, 0.0)),
                    "C": ((1.0, 0.0, 0.0, 0.0),),
                    "D": ((0.0, 0.0),),
                    "x0": (0.0, 0.0, 0.0, 0.0),
                },
                initial_inputs={"u": (1.0, 2.0)},
                input_schedule=(InputUpdate(1.0, {"u": (2.0, 1.0)}),),
                selected_outputs=("y",),
            ),
            on_result_chunk=chunks.append,
            result_chunk_size=2,
        )

        self.assertTrue(result.successful)
        self.assertEqual(result.completion_state, SimulationState.COMPLETED)
        self.assertEqual(result.timestamps, (0.0, 1.0, 2.0))
        self.assertEqual(tuple(len(sample) for sample in result.outputs["y"]), (1, 1, 1))
        self.assertEqual(result.outputs["y"][0], (0.0,))
        self.assertNotEqual(result.outputs["y"][-1], result.outputs["y"][0])
        self.assertEqual([chunk.final_chunk for chunk in chunks], [False, True])
        self.assertEqual(
            tuple(sample for chunk in chunks for sample in chunk.columns["y"]),
            result.outputs["y"],
        )
        self.assertEqual(variables["A"].shape, (3, 3))
        self.assertEqual(variables["y"].shape, (3,))
        self.assertTrue(factory.session._terminated)
        self.assertTrue(factory.session._closed)
        self.assertFalse(factory.extraction_directory.exists())

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "StateSpace-fmi3-patched.csv"
            report = CsvResultExporter().export(result, destination)
            with destination.open("r", encoding="utf-8", newline="") as stream:
                rows = list(csv.reader(stream))

        self.assertEqual(report.row_count, 3)
        self.assertEqual(rows[0], ["time", "y[0]"])
        self.assertEqual(len(rows), 4)

    @unittest.skipUnless(
        bouncing_ball.is_file(), "FMI 3 BouncingBall is unavailable"
    )
    def test_bouncing_ball_fmi3_metadata_reports_event_and_early_return(self) -> None:
        metadata = FmpyImporter().load(self.bouncing_ball)

        self.assertEqual(metadata.fmi_version, "3.0")
        self.assertIn(InterfaceType.CO_SIMULATION, metadata.interface_types)
        self.assertTrue(metadata.capabilities.supports_event_mode)
        self.assertTrue(metadata.capabilities.supports_early_return)
        self.assertTrue(
            any(
                capability.interface_type is InterfaceType.CO_SIMULATION
                and capability.supports_event_mode
                and capability.supports_early_return
                for capability in metadata.interface_capabilities
            )
        )
        self.assertTrue({"h", "v"}.issubset(variable.name for variable in metadata.variables))

    @unittest.skipUnless(
        bouncing_ball.is_file(), "FMI 3 BouncingBall is unavailable"
    )
    def test_bouncing_ball_fmi3_runs_events_and_preserves_result_chunks(self) -> None:
        chunks = []
        result = FarcelEngine(FmpyImporter(), FmpySessionFactory()).run_fmu(
            self.bouncing_ball,
            SimulationConfig(
                start_time=0.0,
                stop_time=3.0,
                communication_step=0.01,
                output_interval=0.01,
                selected_outputs=("h", "v"),
            ),
            on_result_chunk=chunks.append,
            result_chunk_size=32,
        )

        self.assertEqual(result.completion_state, SimulationState.COMPLETED)
        self.assertTrue(result.successful)
        self.assertEqual(result.completed_steps, 300)
        self.assertAlmostEqual(result.final_time, 3.0)
        self.assertTrue(
            all(left < right for left, right in zip(result.timestamps, result.timestamps[1:]))
        )
        self.assertTrue(all(math.isfinite(value) for value in result.outputs["h"]))
        self.assertTrue(all(math.isfinite(value) for value in result.outputs["v"]))
        self.assertLess(min(result.outputs["h"]), 0.1)
        self.assertLess(min(result.outputs["v"]), -1.0)
        self.assertTrue(
            any(
                previous < 0.0 < current
                for previous, current in zip(
                    result.outputs["v"], result.outputs["v"][1:]
                )
            )
        )
        self.assertEqual(
            tuple(time for chunk in chunks for time in chunk.time), result.timestamps
        )
        for name in ("h", "v"):
            self.assertEqual(
                tuple(value for chunk in chunks for value in chunk.columns[name]),
                result.outputs[name],
            )
        self.assertTrue(chunks[-1].final_chunk)

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
    def test_real_fmi3_streams_chunks_matching_the_canonical_result(self) -> None:
        chunks = []
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
            on_result_chunk=chunks.append,
            result_chunk_size=2,
        )

        self.assertEqual([chunk.sequence for chunk in chunks], [0, 1, 2])
        self.assertEqual([len(chunk.time) for chunk in chunks], [2, 2, 1])
        self.assertEqual([chunk.final_chunk for chunk in chunks], [False, False, True])
        self.assertEqual(
            tuple(time for chunk in chunks for time in chunk.time), result.timestamps
        )
        self.assertEqual(
            tuple(value for chunk in chunks for value in chunk.columns["x0"]),
            result.outputs["x0"],
        )

    @unittest.skipUnless(van_der_pol.is_file(), "FMI 3 VanDerPol is unavailable")
    def test_real_fmi3_stop_returns_partial_result(self) -> None:
        control = RunControl()
        factory = CapturingFactory()

        result = FarcelEngine(FmpyImporter(), factory).run_fmu(
            self.van_der_pol,
            SimulationConfig(
                stop_time=0.2,
                communication_step=0.01,
                output_interval=0.05,
                selected_outputs=("x0",),
            ),
            control=control,
            on_progress=lambda progress: (
                control.request_stop() if progress.current_time >= 0.05 else None
            ),
        )

        self.assertEqual(result.completion_state, SimulationState.STOPPED)
        self.assertEqual(result.completed_steps, 5)
        self.assertEqual(result.sample_count, 2)
        self.assertAlmostEqual(result.final_time, 0.05)
        self.assertTrue(factory.session._terminated)
        self.assertTrue(factory.session._closed)
        self.assertFalse(factory.extraction_directory.exists())

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
