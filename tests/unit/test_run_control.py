from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from threading import Thread, get_ident
from unittest.mock import Mock

from farcel import RunControl, RunProgress
from farcel.application.engine import FarcelEngine
from farcel.contracts import (
    CapabilitySet,
    EngineError,
    ErrorCode,
    InterfaceCapability,
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    SimulationState,
    StepResult,
    VariableMetadata,
)
from farcel.infrastructure.export import CsvResultExporter


def executable_metadata() -> ModelMetadata:
    return ModelMetadata(
        model_id="run-control-test",
        source_path="run-control-test.fmu",
        fmi_version="2.0",
        model_name="RunControlTest",
        interface_types=(InterfaceType.CO_SIMULATION,),
        executable_interface=InterfaceType.CO_SIMULATION,
        capabilities=CapabilitySet(can_execute=True),
        interface_capabilities=(
            InterfaceCapability(
                interface_type=InterfaceType.CO_SIMULATION,
                can_execute=True,
                can_handle_variable_step=True,
            ),
        ),
        variables=(VariableMetadata("speed", 1, "Real", causality="output"),),
    )


class _RecordingSession:
    def __init__(self) -> None:
        self.current_time = 0.0
        self.terminated = False
        self.closed = False
        self.output_reads = 0

    def initialize(self) -> None:
        pass

    def step(self, current_time: float, step_size: float) -> StepResult:
        self.current_time = current_time + step_size
        return StepResult(
            requested_time=self.current_time,
            reached_time=self.current_time,
            step_size=step_size,
        )

    def read_outputs(self) -> dict[str, float]:
        self.output_reads += 1
        return {"speed": self.current_time}

    def terminate(self) -> None:
        self.terminated = True

    def close(self) -> None:
        self.closed = True


class RunControlTests(unittest.TestCase):
    def test_existing_run_api_completes_without_control_or_callback(self) -> None:
        engine, session = self._engine()

        result = engine.run_fmu(
            "run-control-test.fmu",
            SimulationConfig(stop_time=0.02, communication_step=0.01),
        )

        self.assertEqual(result.completion_state, SimulationState.COMPLETED)
        self.assertEqual(result.completed_steps, 2)
        self.assertTrue(result.successful)
        self.assertTrue(session.terminated)
        self.assertTrue(session.closed)

    def test_pre_cancel_raises_cancelled_without_loading_or_creating_session(self) -> None:
        control = RunControl()
        control.request_stop()
        importer = Mock()
        factory = Mock()
        engine = FarcelEngine(importer, factory)

        with self.assertRaises(EngineError) as raised:
            engine.run_fmu("run-control-test.fmu", SimulationConfig(), control=control)

        self.assertEqual(raised.exception.code, ErrorCode.CANCELLED)
        importer.load.assert_not_called()
        factory.create.assert_not_called()

    def test_stop_at_sample_point_returns_stopped_without_duplicate_final_sample(self) -> None:
        control = RunControl()
        engine, session = self._engine()

        def stop_at_five_hundredths(progress) -> None:
            if progress.current_time >= 0.05:
                thread = Thread(target=control.request_stop)
                thread.start()
                thread.join()

        result = engine.run_fmu(
            "run-control-test.fmu",
            SimulationConfig(
                stop_time=0.2,
                communication_step=0.01,
                output_interval=0.05,
                selected_outputs=("speed",),
            ),
            control=control,
            on_progress=stop_at_five_hundredths,
        )

        self.assertEqual(result.completion_state, SimulationState.STOPPED)
        self.assertFalse(result.successful)
        self.assertEqual(result.completed_steps, 5)
        self.assertAlmostEqual(result.final_time, 0.05)
        self._assert_timestamps(result.timestamps, (0.0, 0.05))
        self.assertEqual(result.outputs["speed"][-1], result.final_time)
        self.assertTrue(session.terminated)
        self.assertTrue(session.closed)
        self.assertEqual(engine._sessions, {})

    def test_stop_at_unsampled_point_keeps_final_output_and_partial_csv(self) -> None:
        control = RunControl()
        engine, session = self._engine()

        def stop_at_seven_hundredths(progress) -> None:
            if progress.current_time >= 0.07:
                control.request_stop()

        result = engine.run_fmu(
            "run-control-test.fmu",
            SimulationConfig(
                stop_time=0.2,
                communication_step=0.01,
                output_interval=0.05,
                selected_outputs=("speed",),
            ),
            control=control,
            on_progress=stop_at_seven_hundredths,
        )

        self.assertEqual(result.completion_state, SimulationState.STOPPED)
        self.assertEqual(result.completed_steps, 7)
        self.assertAlmostEqual(result.final_time, 0.07)
        self._assert_timestamps(result.timestamps, (0.0, 0.05, 0.07))
        self.assertEqual(len(result.outputs["speed"]), result.sample_count)
        self.assertAlmostEqual(result.outputs["speed"][-1], 0.07)
        self.assertTrue(session.terminated)
        self.assertTrue(session.closed)

        with tempfile.TemporaryDirectory() as directory:
            report = CsvResultExporter().export(result, Path(directory) / "partial.csv")
        self.assertEqual(report.row_count, result.sample_count)

    def test_stopped_run_without_outputs_keeps_partial_timeline(self) -> None:
        control = RunControl()
        engine, session = self._engine()

        result = engine.run_fmu(
            "run-control-test.fmu",
            SimulationConfig(stop_time=0.2, communication_step=0.01, output_interval=0.05),
            control=control,
            on_progress=lambda progress: (
                control.request_stop() if progress.current_time >= 0.07 else None
            ),
        )

        self.assertEqual(result.completion_state, SimulationState.STOPPED)
        self._assert_timestamps(result.timestamps, (0.0, 0.05, 0.07))
        self.assertEqual(result.outputs, {})
        self.assertEqual(session.output_reads, 0)

    def test_progress_is_monotonic_and_has_completed_terminal_event(self) -> None:
        progress_events = []
        callback_threads = []
        engine, _ = self._engine()

        engine.run_fmu(
            "run-control-test.fmu",
            SimulationConfig(stop_time=0.02, communication_step=0.01),
            on_progress=lambda progress: (
                progress_events.append(progress), callback_threads.append(get_ident())
            ),
        )

        self.assertEqual(progress_events[-1].state, SimulationState.COMPLETED)
        self.assertIsInstance(progress_events[-1], RunProgress)
        self.assertEqual(progress_events[-1].fraction, 1.0)
        self.assertTrue(
            all(
                left.current_time <= right.current_time
                and left.completed_steps <= right.completed_steps
                for left, right in zip(progress_events, progress_events[1:])
            )
        )
        self.assertTrue(all(0.0 <= event.fraction <= 1.0 for event in progress_events))
        self.assertEqual(callback_threads, [get_ident()] * len(callback_threads))

    def test_stopped_run_has_stopped_terminal_progress_before_completion(self) -> None:
        control = RunControl()
        progress_events = []
        engine, _ = self._engine()

        engine.run_fmu(
            "run-control-test.fmu",
            SimulationConfig(stop_time=0.2, communication_step=0.01),
            control=control,
            on_progress=lambda progress: (
                progress_events.append(progress),
                control.request_stop() if progress.current_time >= 0.05 else None,
            ),
        )

        self.assertEqual(progress_events[-1].state, SimulationState.STOPPED)
        self.assertLess(progress_events[-1].fraction, 1.0)

    def test_callback_exception_maps_to_engine_error_and_still_cleans_up(self) -> None:
        engine, session = self._engine()

        with self.assertRaises(EngineError) as raised:
            engine.run_fmu(
                "run-control-test.fmu",
                SimulationConfig(stop_time=0.02, communication_step=0.01),
                on_progress=lambda progress: (_ for _ in ()).throw(RuntimeError("boom")),
            )

        self.assertEqual(raised.exception.code, ErrorCode.INTERNAL_ERROR)
        self.assertEqual(raised.exception.details["callback_diagnostic"], "boom")
        self.assertTrue(session.terminated)
        self.assertTrue(session.closed)
        self.assertEqual(engine._sessions, {})

    def _assert_timestamps(
        self, actual: tuple[float, ...], expected: tuple[float, ...]
    ) -> None:
        self.assertEqual(len(actual), len(expected))
        for observed, target in zip(actual, expected):
            self.assertAlmostEqual(observed, target)

    @staticmethod
    def _engine() -> tuple[FarcelEngine, _RecordingSession]:
        session = _RecordingSession()
        metadata = executable_metadata()
        importer = type("Importer", (), {"load": lambda _, path: metadata})()
        factory = type("Factory", (), {"create": lambda _, metadata, config: session})()
        return FarcelEngine(importer, factory), session
