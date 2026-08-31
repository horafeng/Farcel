from __future__ import annotations

import unittest
from unittest.mock import patch

from farcel import ResultChunk
from farcel.application.engine import FarcelEngine
from farcel.contracts import (
    CapabilitySet,
    EngineError,
    ErrorCode,
    InputUpdate,
    InterfaceCapability,
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    StepResult,
    VariableMetadata,
)


def early_return_metadata() -> ModelMetadata:
    return ModelMetadata(
        model_id="early-return-grid",
        source_path="early-return-grid.fmu",
        fmi_version="3.0",
        model_name="EarlyReturnGrid",
        interface_types=(InterfaceType.CO_SIMULATION,),
        executable_interface=InterfaceType.CO_SIMULATION,
        capabilities=CapabilitySet(can_execute=True, supports_early_return=True),
        interface_capabilities=(
            InterfaceCapability(
                interface_type=InterfaceType.CO_SIMULATION,
                can_execute=True,
                can_handle_variable_step=True,
                supports_early_return=True,
            ),
        ),
        variables=(
            VariableMetadata("gain", 1, "Float64", causality="input"),
            VariableMetadata("speed", 2, "Float64", causality="output"),
        ),
    )


class _EarlyReturnSession:
    def __init__(self) -> None:
        self.step_calls: list[tuple[float, float]] = []
        self.input_updates: list[dict[str, float]] = []
        self.current_time = 0.0
        self.terminated = False
        self.closed = False

    def initialize(self) -> None:
        pass

    def step(self, current_time: float, step_size: float) -> StepResult:
        self.step_calls.append((current_time, step_size))
        if len(self.step_calls) == 1:
            self.current_time = 0.004
            return StepResult(
                requested_time=current_time + step_size,
                reached_time=self.current_time,
                step_size=step_size,
                early_return=True,
            )
        self.current_time = current_time + step_size
        return StepResult(
            requested_time=self.current_time,
            reached_time=self.current_time,
            step_size=step_size,
        )

    def set_inputs(self, values: dict[str, float]) -> None:
        self.input_updates.append(dict(values))

    def read_outputs(self) -> dict[str, float]:
        return {"speed": self.current_time}

    def terminate(self) -> None:
        self.terminated = True

    def close(self) -> None:
        self.closed = True


class _NoProgressEarlyReturnSession(_EarlyReturnSession):
    def step(self, current_time: float, step_size: float) -> StepResult:
        self.step_calls.append((current_time, step_size))
        return StepResult(
            requested_time=current_time + step_size,
            reached_time=current_time,
            step_size=step_size,
            early_return=True,
        )


class _FragmentingEarlyReturnSession(_EarlyReturnSession):
    def step(self, current_time: float, step_size: float) -> StepResult:
        self.step_calls.append((current_time, step_size))
        self.current_time = current_time + 0.001
        return StepResult(
            requested_time=current_time + step_size,
            reached_time=self.current_time,
            step_size=step_size,
            early_return=True,
        )


class _Factory:
    def __init__(self, session: _EarlyReturnSession) -> None:
        self.session = session

    def create(self, metadata, config) -> _EarlyReturnSession:
        return self.session


class EarlyReturnGridTests(unittest.TestCase):
    def test_early_return_preserves_grid_inputs_sampling_and_chunks(self) -> None:
        session = _EarlyReturnSession()
        engine = self._engine(session)
        chunks: list[ResultChunk] = []
        progress_times: list[float] = []

        result = engine.run_fmu(
            "early-return-grid.fmu",
            SimulationConfig(
                stop_time=0.03,
                communication_step=0.01,
                output_interval=0.01,
                selected_outputs=("speed",),
                input_schedule=(InputUpdate(0.01, {"gain": 2.0}),),
            ),
            on_progress=lambda progress: progress_times.append(progress.current_time),
            on_result_chunk=chunks.append,
            result_chunk_size=2,
        )

        self._assert_calls(
            session.step_calls,
            ((0.0, 0.01), (0.004, 0.006), (0.01, 0.01), (0.02, 0.01)),
        )
        self.assertEqual(session.input_updates, [{"gain": 2.0}])
        self.assertEqual(result.completed_steps, 3)
        self._assert_times(result.timestamps, (0.0, 0.01, 0.02, 0.03))
        self.assertIn(0.004, progress_times)
        self.assertEqual(
            tuple(time for chunk in chunks for time in chunk.time), result.timestamps
        )
        self.assertEqual(
            tuple(value for chunk in chunks for value in chunk.columns["speed"]),
            result.outputs["speed"],
        )
        self.assertTrue(chunks[-1].final_chunk)

    def test_no_progress_early_return_is_step_error_and_cleans_up(self) -> None:
        session = _NoProgressEarlyReturnSession()
        engine = self._engine(session)

        with self.assertRaises(EngineError) as raised:
            engine.run_fmu(
                "early-return-grid.fmu",
                SimulationConfig(stop_time=0.01, communication_step=0.01),
            )

        self.assertEqual(raised.exception.code, ErrorCode.STEP_ERROR)
        self.assertTrue(raised.exception.details["early_return"])
        self.assertTrue(session.terminated)
        self.assertTrue(session.closed)
        self.assertEqual(engine._sessions, {})

    def test_repeated_early_returns_hit_per_target_guard_and_clean_up(self) -> None:
        session = _FragmentingEarlyReturnSession()
        engine = self._engine(session)

        with patch(
            "farcel.application.engine._MAX_STEP_ATTEMPTS_PER_COMMUNICATION_TARGET",
            2,
        ):
            with self.assertRaises(EngineError) as raised:
                engine.run_fmu(
                    "early-return-grid.fmu",
                    SimulationConfig(stop_time=0.01, communication_step=0.01),
                )

        self.assertEqual(raised.exception.code, ErrorCode.STEP_ERROR)
        self.assertEqual(raised.exception.details["step_attempt_count"], 3)
        self.assertTrue(session.terminated)
        self.assertTrue(session.closed)
        self.assertEqual(engine._sessions, {})

    @staticmethod
    def _engine(session: _EarlyReturnSession) -> FarcelEngine:
        metadata = early_return_metadata()
        importer = type("Importer", (), {"load": lambda _, path: metadata})()
        return FarcelEngine(importer, _Factory(session))

    def _assert_calls(
        self,
        actual: list[tuple[float, float]],
        expected: tuple[tuple[float, float], ...],
    ) -> None:
        self.assertEqual(len(actual), len(expected))
        for observed, target in zip(actual, expected):
            self.assertAlmostEqual(observed[0], target[0])
            self.assertAlmostEqual(observed[1], target[1])

    def _assert_times(
        self, actual: tuple[float, ...], expected: tuple[float, ...]
    ) -> None:
        self.assertEqual(len(actual), len(expected))
        for observed, target in zip(actual, expected):
            self.assertAlmostEqual(observed, target)


if __name__ == "__main__":
    unittest.main()
