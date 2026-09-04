from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from farcel import RunControl, create_backend
from farcel.cli import main as cli_main
from farcel.contracts import EngineError, ErrorCode, InputUpdate, InterfaceType, SimulationConfig, SimulationState


FMU_FIXTURES = Path(__file__).resolve().parents[2] / "examples" / "fmus"


class PublicModelExchangeTests(unittest.TestCase):
    van_der_pol = FMU_FIXTURES / "VanDerPol.fmu"
    stair = FMU_FIXTURES / "Stair.fmu"
    bouncing_ball = FMU_FIXTURES / "BouncingBall-fmi2.fmu"
    feedthrough = FMU_FIXTURES / "Feedthrough-fmi2.fmu"
    van_der_pol_fmi3 = FMU_FIXTURES / "VanDerPol-fmi3.fmu"

    def _run(self, path: Path, config: SimulationConfig, **kwargs):
        return create_backend().run_fmu(path, config, **kwargs)

    def test_public_fmi2_model_exchange_reference_matrix(self) -> None:
        cases = (
            (
                self.van_der_pol,
                SimulationConfig(stop_time=.05, communication_step=.01, selected_outputs=("x0",), execution_interface=InterfaceType.MODEL_EXCHANGE),
                5,
            ),
            (
                self.stair,
                SimulationConfig(stop_time=1.2, communication_step=.2, selected_outputs=("counter",), execution_interface=InterfaceType.MODEL_EXCHANGE),
                6,
            ),
            (
                self.bouncing_ball,
                SimulationConfig(stop_time=1.0, communication_step=.05, selected_outputs=("h", "v"), execution_interface=InterfaceType.MODEL_EXCHANGE),
                20,
            ),
            (
                self.feedthrough,
                SimulationConfig(stop_time=.04, communication_step=.01, initial_inputs={"Float64_continuous_input": 1.0}, input_schedule=(InputUpdate(.02, {"Float64_continuous_input": 4.0}),), selected_outputs=("Float64_continuous_output",), execution_interface=InterfaceType.MODEL_EXCHANGE),
                4,
            ),
        )
        results = []
        for path, config, completed_steps in cases:
            result = self._run(path, config)
            self.assertIs(result.completion_state, SimulationState.COMPLETED)
            self.assertEqual(result.completed_steps, completed_steps)
            results.append(result)
        self.assertEqual(results[1].outputs["counter"][-1], 2)
        self.assertTrue(any(value < 0 for value in results[2].outputs["v"]))
        self.assertEqual(results[3].outputs["Float64_continuous_output"][-1], 4.0)

    def test_public_me_stop_progress_chunks_and_csv(self) -> None:
        control = RunControl()
        progress = []
        chunks = []

        def request_stop(item) -> None:
            progress.append(item)
            if item.current_time >= .02:
                control.request_stop()

        config = SimulationConfig(stop_time=.05, communication_step=.01, selected_outputs=("x0",), execution_interface=InterfaceType.MODEL_EXCHANGE)
        result = self._run(self.van_der_pol, config, control=control, on_progress=request_stop, on_result_chunk=chunks.append, result_chunk_size=2)
        self.assertIs(result.completion_state, SimulationState.STOPPED)
        self.assertEqual((result.final_time, result.completed_steps), (.02, 2))
        self.assertEqual(sum(chunk.final_chunk for chunk in chunks), 1)
        self.assertEqual([chunk.sequence for chunk in chunks], list(range(len(chunks))))
        self.assertEqual(progress[0].state, SimulationState.RUNNING)
        self.assertEqual(progress[-1].state, SimulationState.STOPPED)
        with tempfile.TemporaryDirectory() as directory:
            report = create_backend().export_result(result, Path(directory) / "me.csv")
            self.assertEqual(report.row_count, result.sample_count)

    def test_public_me_pre_start_cancel_and_fmi3_rejection(self) -> None:
        control = RunControl(); control.request_stop()
        with self.assertRaises(EngineError) as raised:
            self._run(self.van_der_pol, SimulationConfig(execution_interface=InterfaceType.MODEL_EXCHANGE), control=control)
        self.assertEqual(raised.exception.code, ErrorCode.CANCELLED)
        with self.assertRaises(EngineError) as raised:
            self._run(self.van_der_pol_fmi3, SimulationConfig(execution_interface=InterfaceType.MODEL_EXCHANGE))
        self.assertEqual(raised.exception.code, ErrorCode.CONFIG_ERROR)
        self.assertEqual(raised.exception.details["issues"][0]["code"], ErrorCode.UNSUPPORTED_INTERFACE.value)

    def test_cli_public_model_exchange_validate_run_export_and_fmi3_reject(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            export_path = Path(directory) / "me.csv"
            for command in (
                ["validate", str(self.van_der_pol), "--interface", "model_exchange", "--stop-time", ".05", "--step-size", ".01", "--output", "x0"],
                ["run", str(self.van_der_pol), "--interface", "model_exchange", "--stop-time", ".05", "--step-size", ".01", "--output", "x0"],
                ["export", str(self.van_der_pol), "--interface", "model_exchange", "--stop-time", ".05", "--step-size", ".01", "--output", "x0", "--csv", str(export_path)],
            ):
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    self.assertEqual(cli_main(command), 0)
            self.assertTrue(export_path.is_file())
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(cli_main(["validate", str(self.van_der_pol_fmi3), "--interface", "model_exchange"]), 1)
