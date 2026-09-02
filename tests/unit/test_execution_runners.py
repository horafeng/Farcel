from __future__ import annotations

import unittest
from unittest.mock import Mock

from farcel.application.engine import FarcelEngine
from farcel.application.runners import CoSimulationRunner
from farcel.contracts import (
    CapabilitySet,
    EngineError,
    ErrorCode,
    InterfaceCapability,
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    SimulationResult,
    SimulationState,
    StepResult,
    StepStatus,
    VariableMetadata,
)


def _metadata() -> ModelMetadata:
    return ModelMetadata(
        model_id="runner-test",
        source_path="runner-test.fmu",
        fmi_version="2.0",
        model_name="RunnerTest",
        interface_types=(InterfaceType.CO_SIMULATION, InterfaceType.MODEL_EXCHANGE),
        executable_interface=InterfaceType.CO_SIMULATION,
        capabilities=CapabilitySet(can_execute=True),
        interface_capabilities=(
            InterfaceCapability(
                interface_type=InterfaceType.CO_SIMULATION,
                can_execute=True,
                can_handle_variable_step=True,
            ),
            InterfaceCapability(
                interface_type=InterfaceType.MODEL_EXCHANGE,
                can_execute=False,
            ),
        ),
        variables=(VariableMetadata("speed", 1, "Real", causality="output"),),
    )


def _result() -> SimulationResult:
    return SimulationResult(
        fmu_path="runner-test.fmu",
        start_time=0.0,
        stop_time=0.1,
        step_size=0.01,
        completed_steps=10,
        final_time=0.1,
        completion_state=SimulationState.COMPLETED,
        timestamps=(0.0, 0.1),
        outputs={},
    )


class _LifecycleSession:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self._current_time = 0.0

    def initialize(self) -> None:
        self._events.append("initialize")

    def step(self, current_time: float, step_size: float) -> StepResult:
        self._events.append(f"step:{current_time:.2f}:{step_size:.2f}")
        self._current_time = current_time + step_size
        return StepResult(
            requested_time=self._current_time,
            reached_time=self._current_time,
            step_size=step_size,
        )

    def read_outputs(self) -> dict[str, float]:
        self._events.append(f"read:{self._current_time:.2f}")
        return {"speed": self._current_time}

    def terminate(self) -> None:
        self._events.append("terminate")

    def close(self) -> None:
        self._events.append("close")


class ExecutionRunnerTests(unittest.TestCase):
    def test_default_interface_dispatches_to_co_simulation_runner(self) -> None:
        engine, co_simulation, model_exchange, _ = self._engine_with_spies()

        result = engine.run_fmu("runner-test.fmu", SimulationConfig())

        self.assertIs(result, co_simulation.run.return_value)
        co_simulation.run.assert_called_once()
        model_exchange.run.assert_not_called()

    def test_explicit_co_simulation_dispatches_to_same_runner(self) -> None:
        engine, co_simulation, model_exchange, _ = self._engine_with_spies()

        result = engine.run_fmu(
            "runner-test.fmu",
            SimulationConfig(execution_interface=InterfaceType.CO_SIMULATION),
        )

        self.assertIs(result, co_simulation.run.return_value)
        co_simulation.run.assert_called_once()
        model_exchange.run.assert_not_called()

    def test_explicit_model_exchange_fails_before_any_runner_or_native_factory(self) -> None:
        engine, co_simulation, model_exchange, factory = self._engine_with_spies()

        with self.assertRaises(EngineError) as raised:
            engine.run_fmu(
                "runner-test.fmu",
                SimulationConfig(execution_interface=InterfaceType.MODEL_EXCHANGE),
            )

        self.assertEqual(raised.exception.code, ErrorCode.CONFIG_ERROR)
        self.assertEqual(
            raised.exception.details["issues"][0]["code"],
            ErrorCode.UNSUPPORTED_INTERFACE.value,
        )
        co_simulation.run.assert_not_called()
        model_exchange.run.assert_not_called()
        factory.create.assert_not_called()

    def test_co_simulation_runner_preserves_lifecycle_sampling_and_callback_order(self) -> None:
        events: list[str] = []
        session = _LifecycleSession(events)
        factory = Mock()
        factory.create.side_effect = lambda metadata, config: (
            events.append("create") or session
        )

        result = CoSimulationRunner(factory).run(
            "runner-test.fmu",
            _metadata(),
            SimulationConfig(
                stop_time=0.02,
                communication_step=0.01,
                selected_outputs=("speed",),
            ),
            on_progress=lambda progress: events.append(
                f"progress:{progress.state.value}:{progress.current_time:.2f}"
            ),
            on_result_chunk=lambda chunk: events.append(
                f"chunk:{chunk.sequence}:{chunk.final_chunk}:{tuple(chunk.time)}"
            ),
            result_chunk_size=2,
        )

        self.assertEqual(result.timestamps, (0.0, 0.01, 0.02))
        self.assertEqual(result.outputs, {"speed": (0.0, 0.01, 0.02)})
        self.assertEqual(
            events,
            [
                "create",
                "initialize",
                "read:0.00",
                "progress:running:0.00",
                "step:0.00:0.01",
                "read:0.01",
                "progress:running:0.01",
                "step:0.01:0.01",
                "read:0.02",
                "chunk:0:False:(0.0, 0.01)",
                "progress:running:0.02",
                "terminate",
                "chunk:1:True:(0.02,)",
                "progress:completed:0.02",
                "close",
            ],
        )

    def test_low_level_session_api_step_remains_compatible_after_runner_refactor(self) -> None:
        events: list[str] = []
        session = _LifecycleSession(events)
        importer = Mock()
        metadata = _metadata()
        importer.load.return_value = metadata
        factory = Mock()
        factory.create.return_value = session
        engine = FarcelEngine(importer, factory)
        config = SimulationConfig(selected_outputs=("speed",))

        loaded = engine.load_fmu("runner-test.fmu")
        handle = engine.create_session(loaded.model_id, config)
        engine.initialize(handle)
        default_step = engine.step(handle)
        outputs = engine.read_outputs(handle)
        explicit_step = engine.step(handle, step_size=0.02)
        engine.terminate(handle)
        self.assertIs(engine.get_state(handle), SimulationState.STOPPED)
        engine.close_session(handle)
        engine.close_session(handle)

        self.assertIs(default_step.status, StepStatus.SUCCESS)
        self.assertGreater(default_step.reached_time, config.start_time)
        self.assertEqual(default_step.step_size, config.communication_step)
        self.assertEqual(outputs, {"speed": config.communication_step})
        self.assertIs(explicit_step.status, StepStatus.SUCCESS)
        self.assertEqual(explicit_step.step_size, 0.02)
        self.assertNotIn(handle.session_id, engine._sessions)
        factory.create.assert_called_once_with(metadata, config)
        self.assertEqual(
            events,
            [
                "initialize",
                "step:0.00:0.01",
                "read:0.01",
                "step:0.01:0.02",
                "terminate",
                "close",
            ],
        )

    @staticmethod
    def _engine_with_spies() -> tuple[FarcelEngine, Mock, Mock, Mock]:
        importer = Mock()
        importer.load.return_value = _metadata()
        factory = Mock()
        engine = FarcelEngine(importer, factory)
        co_simulation = Mock()
        co_simulation.run.return_value = _result()
        model_exchange = Mock()
        engine._co_simulation_runner = co_simulation
        engine._model_exchange_runner = model_exchange
        return engine, co_simulation, model_exchange, factory
