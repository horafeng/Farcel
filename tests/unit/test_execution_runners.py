from __future__ import annotations

import unittest
from unittest.mock import Mock

from farcel.application.engine import FarcelEngine
from farcel.application.runners import CoSimulationRunner, ModelExchangeRunner
from farcel.contracts import (
    CapabilitySet,
    DiscreteStateUpdate,
    EngineError,
    ErrorCode,
    InterfaceCapability,
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    SimulationResult,
    SimulationState,
    SolverAdvanceResult,
    SolverAdvanceStatus,
    StepResult,
    StepStatus,
    VariableMetadata,
)
from farcel import RunControl


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


class _ModelExchangeSession:
    def __init__(self, events: list[str], *, terminate_on_step: int | None = None,
                 terminate_during_initialization: bool = False) -> None:
        self.events = events
        self.current_time = 0.0
        self.terminate_on_step = terminate_on_step
        self.terminate_during_initialization = terminate_during_initialization
        self.completed_calls = 0

    def initialize(self):
        from farcel.contracts import ModelExchangeInitialization

        self.events.append("initialize")
        return ModelExchangeInitialization(1, 0, terminate_requested=self.terminate_during_initialization)

    def get_initial_time(self): return 0.0
    def get_event_indicator_count(self): return 0
    def set_inputs(self, values): self.events.append(f"input:{values}")
    def set_time(self, value): self.current_time = value
    def get_continuous_states(self): return (self.current_time,)
    def get_nominals_of_continuous_states(self): return (1.0,)
    def set_continuous_states(self, states): self.current_time = states[0]
    def get_derivatives(self): return (1.0,)
    def get_event_indicators(self): return ()
    def completed_integrator_step(self):
        from farcel.contracts import IntegratorStepResult

        self.completed_calls += 1
        return IntegratorStepResult(
            terminate_requested=self.completed_calls == self.terminate_on_step
        )
    def enter_event_mode(self): self.events.append("event")
    def update_discrete_states(self): return DiscreteStateUpdate(False)
    def enter_continuous_time_mode(self): self.events.append("continuous")
    def read_outputs(self):
        self.events.append(f"read:{self.current_time:.2f}")
        return {"speed": self.current_time}
    def terminate(self): self.events.append("terminate")
    def close(self): self.events.append("close")


class _ModelExchangeSolver:
    def __init__(self, events: list[str], *, stop_control: RunControl | None = None,
                 fail_initialize: bool = False, fail_close: bool = False) -> None:
        self.events = events
        self.stop_control = stop_control
        self.fail_initialize = fail_initialize
        self.fail_close = fail_close
        self.problem = None
        self.options = None

    def initialize(self, problem, options):
        self.events.append("solver.initialize")
        self.problem, self.options = problem, options
        if self.fail_initialize:
            raise RuntimeError("solver initialize failed")

    def integrate_to(self, target):
        self.events.append(f"solver.integrate:{target:.2f}")
        self.problem.set_state(target, (target,))
        if self.stop_control is not None:
            self.stop_control.request_stop()
        return SolverAdvanceResult(target, SolverAdvanceStatus.REACHED_TARGET)

    def reset(self, time, reason): self.events.append("solver.reset")
    def close(self):
        self.events.append("solver.close")
        if self.fail_close:
            raise RuntimeError("solver close failed")


def _me_metadata(*, needs_completed_integrator_step: bool = False) -> ModelMetadata:
    metadata = _metadata()
    return ModelMetadata(
        model_id=metadata.model_id,
        source_path=metadata.source_path,
        fmi_version=metadata.fmi_version,
        model_name=metadata.model_name,
        interface_types=metadata.interface_types,
        executable_interface=metadata.executable_interface,
        capabilities=metadata.capabilities,
        interface_capabilities=(
            metadata.interface_capabilities[0],
            InterfaceCapability(
                interface_type=InterfaceType.MODEL_EXCHANGE,
                can_execute=False,
                needs_completed_integrator_step=needs_completed_integrator_step,
            ),
        ),
        variables=metadata.variables,
    )


class ExecutionRunnerTests(unittest.TestCase):
    def test_model_exchange_runner_requires_both_internal_dependencies(self) -> None:
        with self.assertRaises(EngineError) as raised:
            ModelExchangeRunner(None, None).run("runner-test.fmu", _me_metadata(), SimulationConfig())
        self.assertEqual(raised.exception.code, ErrorCode.NOT_IMPLEMENTED)

    def test_model_exchange_runner_records_checkpoint_grid_endpoint_progress_and_chunks(self) -> None:
        events: list[str] = []
        session = _ModelExchangeSession(events)
        session_factory = Mock(); session_factory.create.return_value = session
        solver = _ModelExchangeSolver(events)
        solver_factory = Mock(); solver_factory.create.return_value = solver
        progress = []; chunks = []

        result = ModelExchangeRunner(session_factory, solver_factory).run(
            "runner-test.fmu",
            _me_metadata(),
            SimulationConfig(stop_time=.05, communication_step=.01, output_interval=.02,
                             selected_outputs=("speed",)),
            on_progress=progress.append,
            on_result_chunk=chunks.append,
            result_chunk_size=2,
        )

        self.assertEqual(result.timestamps, (0.0, .02, .04, .05))
        self.assertEqual(result.outputs, {"speed": (0.0, .02, .04, .05)})
        self.assertEqual(result.completed_steps, 5)
        self.assertEqual(result.final_time, .05)
        self.assertIs(result.completion_state, SimulationState.COMPLETED)
        self.assertEqual([item.current_time for item in progress], [0.0, .01, .02, .03, .04, .05, .05])
        self.assertEqual([item.state for item in progress], [SimulationState.RUNNING] * 6 + [SimulationState.COMPLETED])
        self.assertEqual([(item.sequence, item.final_chunk, item.time) for item in chunks], [(0, False, (0.0, .02)), (1, True, (.04, .05))])
        self.assertEqual(events[-3:], ["terminate", "solver.close", "close"])
        self.assertEqual(solver.options.relative_tolerance, 1e-5)
        self.assertIsNone(solver.options.maximum_step)

    def test_model_exchange_runner_allows_partial_final_checkpoint_and_empty_outputs(self) -> None:
        events: list[str] = []
        session_factory = Mock(); session_factory.create.return_value = _ModelExchangeSession(events)
        solver_factory = Mock(); solver_factory.create.return_value = _ModelExchangeSolver(events)
        result = ModelExchangeRunner(session_factory, solver_factory).run(
            "runner-test.fmu", _me_metadata(), SimulationConfig(stop_time=.05, communication_step=.02)
        )
        self.assertEqual(result.timestamps, (0.0, .02, .04, .05))
        self.assertEqual(result.outputs, {})
        self.assertEqual(result.completed_steps, 3)

    def test_model_exchange_runner_stop_after_solver_response_returns_partial_result(self) -> None:
        events: list[str] = []
        control = RunControl()
        session_factory = Mock(); session_factory.create.return_value = _ModelExchangeSession(events)
        solver_factory = Mock(); solver_factory.create.return_value = _ModelExchangeSolver(events, stop_control=control)
        result = ModelExchangeRunner(session_factory, solver_factory).run(
            "runner-test.fmu", _me_metadata(), SimulationConfig(stop_time=.05, communication_step=.01,
            selected_outputs=("speed",)), control=control,
        )
        self.assertIs(result.completion_state, SimulationState.STOPPED)
        self.assertEqual((result.final_time, result.completed_steps, result.timestamps), (.01, 1, (0.0, .01)))

    def test_model_exchange_runner_fmu_terminate_requested_is_early_completed_result(self) -> None:
        events: list[str] = []
        session_factory = Mock(); session_factory.create.return_value = _ModelExchangeSession(events, terminate_on_step=1)
        solver_factory = Mock(); solver_factory.create.return_value = _ModelExchangeSolver(events)
        result = ModelExchangeRunner(session_factory, solver_factory).run(
            "runner-test.fmu", _me_metadata(needs_completed_integrator_step=True),
            SimulationConfig(stop_time=.05, communication_step=.01, selected_outputs=("speed",)),
        )
        self.assertIs(result.completion_state, SimulationState.COMPLETED)
        self.assertEqual((result.final_time, result.completed_steps, result.timestamps), (.01, 0, (0.0, .01)))

    def test_model_exchange_runner_initialization_termination_does_not_create_solver(self) -> None:
        events: list[str] = []
        session_factory = Mock(); session_factory.create.return_value = _ModelExchangeSession(
            events, terminate_during_initialization=True
        )
        solver_factory = Mock()
        result = ModelExchangeRunner(session_factory, solver_factory).run(
            "runner-test.fmu", _me_metadata(), SimulationConfig(selected_outputs=("speed",))
        )
        self.assertIs(result.completion_state, SimulationState.COMPLETED)
        self.assertEqual((result.final_time, result.completed_steps, result.timestamps), (0.0, 0, (0.0,)))
        solver_factory.create.assert_not_called()
        self.assertEqual(events[-2:], ["terminate", "close"])

    def test_model_exchange_runner_callback_error_and_solver_initialization_error_cleanup_every_component(self) -> None:
        events: list[str] = []
        session_factory = Mock(); session_factory.create.return_value = _ModelExchangeSession(events)
        solver_factory = Mock(); solver_factory.create.return_value = _ModelExchangeSolver(events, fail_initialize=True)
        with self.assertRaises(EngineError) as raised:
            ModelExchangeRunner(session_factory, solver_factory).run(
                "runner-test.fmu", _me_metadata(), SimulationConfig(), on_progress=lambda _: (_ for _ in ()).throw(RuntimeError("progress failed"))
            )
        self.assertEqual(raised.exception.code, ErrorCode.INTERNAL_ERROR)
        self.assertEqual(events[-2:], ["terminate", "close"])

        events = []
        session_factory.create.return_value = _ModelExchangeSession(events)
        solver_factory.create.return_value = _ModelExchangeSolver(events, fail_initialize=True)
        with self.assertRaises(EngineError) as raised:
            ModelExchangeRunner(session_factory, solver_factory).run("runner-test.fmu", _me_metadata(), SimulationConfig())
        self.assertEqual(raised.exception.code, ErrorCode.INTERNAL_ERROR)
        self.assertEqual(events[-3:], ["terminate", "solver.close", "close"])

    def test_model_exchange_runner_primary_error_preserves_precedence_over_solver_cleanup_error(self) -> None:
        events: list[str] = []
        session_factory = Mock(); session_factory.create.return_value = _ModelExchangeSession(events)
        solver_factory = Mock(); solver_factory.create.return_value = _ModelExchangeSolver(events, fail_close=True)
        with self.assertRaises(EngineError) as raised:
            ModelExchangeRunner(session_factory, solver_factory).run(
                "runner-test.fmu", _me_metadata(), SimulationConfig(),
                on_result_chunk=lambda _: (_ for _ in ()).throw(RuntimeError("chunk failed")), result_chunk_size=1,
            )
        self.assertEqual(raised.exception.code, ErrorCode.INTERNAL_ERROR)
        self.assertEqual(raised.exception.details["cleanup_error"]["code"], ErrorCode.CLEANUP_ERROR.value)
        self.assertEqual(events[-3:], ["terminate", "solver.close", "close"])
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
