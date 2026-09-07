from __future__ import annotations

import unittest

from farcel.application.model_exchange_problem import SessionModelExchangeProblem
from farcel.application.model_exchange_runtime import ModelExchangeCheckpointOutcome
from farcel.application.node_runtime import (
    ModelExchangeNodeRuntime,
    ModelExchangeNodeRuntimeFactory,
)
from farcel.contracts import (
    CapabilitySet,
    DiscreteStateUpdate,
    EngineError,
    ErrorCode,
    InterfaceCapability,
    InterfaceType,
    ModelExchangeInitialization,
    ModelMetadata,
    SimulationConfig,
    SolverOptions,
)


def _metadata(*, fmi_version: str = "2.0", interface: InterfaceType = InterfaceType.MODEL_EXCHANGE, can_execute: bool = True, needs_completed: bool = False) -> ModelMetadata:
    return ModelMetadata(
        model_id="me-node-runtime", source_path="me-node-runtime.fmu", fmi_version=fmi_version,
        model_name="MeNodeRuntime", interface_types=(interface,),
        executable_interface=interface if can_execute else None,
        capabilities=CapabilitySet(can_execute=can_execute),
        interface_capabilities=(InterfaceCapability(interface, can_execute=can_execute, needs_completed_integrator_step=needs_completed),),
    )


class _Session:
    def __init__(self, initialization: ModelExchangeInitialization | None = None) -> None:
        self.initialization = initialization or ModelExchangeInitialization(1, 0)
        self.initialize_calls = self.terminate_calls = self.close_calls = 0
        self.inputs: list[dict[str, object]] = []
        self.events: list[str] = []
        self.terminate_error: BaseException | None = None
        self.close_error: BaseException | None = None
        self.outputs = {"y": 1.0}

    def initialize(self): self.initialize_calls += 1; return self.initialization
    def get_initial_time(self): return 0.0
    def get_event_indicator_count(self): return 0
    def set_inputs(self, values): self.inputs.append(dict(values))
    def set_time(self, time): pass
    def get_continuous_states(self): return (0.0,)
    def get_nominals_of_continuous_states(self): return (1.0,)
    def set_continuous_states(self, states): pass
    def get_derivatives(self): return (0.0,)
    def get_event_indicators(self): return ()
    def completed_integrator_step(self): raise AssertionError("not expected")
    def enter_event_mode(self): self.events.append("event")
    def update_discrete_states(self): self.events.append("update"); return DiscreteStateUpdate(False)
    def enter_continuous_time_mode(self): self.events.append("continuous")
    def read_outputs(self): return self.outputs
    def terminate(self):
        self.terminate_calls += 1
        if self.terminate_error is not None: raise self.terminate_error
    def close(self):
        self.close_calls += 1
        if self.close_error is not None: raise self.close_error


class _Solver:
    def __init__(self) -> None:
        self.initialize_calls: list[tuple[object, SolverOptions]] = []
        self.integrate_calls: list[float] = []
        self.close_calls = 0
        self.initialize_error: BaseException | None = None
        self.close_error: BaseException | None = None

    def initialize(self, problem, options):
        self.initialize_calls.append((problem, options))
        if self.initialize_error is not None: raise self.initialize_error
    def integrate_to(self, target): self.integrate_calls.append(target); raise AssertionError("not expected")
    def reset(self, time, reason): pass
    def close(self):
        self.close_calls += 1
        if self.close_error is not None: raise self.close_error


class _SessionFactory:
    def __init__(self, session): self.session = session; self.calls = []
    def create(self, metadata, config): self.calls.append((metadata, config)); return self.session


class _SolverFactory:
    def __init__(self, solver): self.solver = solver; self.calls = 0; self.error = None
    def create(self):
        self.calls += 1
        if self.error is not None: raise self.error
        return self.solver


class _Coordinator:
    def __init__(self, *, current_time=0.0, input_terminate=False, outcome=None):
        self.current_time = current_time; self.input_terminate = input_terminate
        self.outcome = outcome or ModelExchangeCheckpointOutcome(current_time, True)
        self.inputs = []; self.targets = []
    def apply_inputs(self, values): self.inputs.append(dict(values)); return self.input_terminate
    def advance_to(self, target): self.targets.append(target); return self.outcome


class ModelExchangeNodeRuntimeTests(unittest.TestCase):
    def test_factory_creates_uninitialized_and_forwards_capability(self):
        session, solver = _Session(), _Solver()
        metadata = _metadata(needs_completed=True)
        runtime = ModelExchangeNodeRuntimeFactory(_SessionFactory(session), _SolverFactory(solver)).create(metadata, self._config())
        self.assertIsInstance(runtime, ModelExchangeNodeRuntime)
        self.assertEqual((session.initialize_calls, solver.initialize_calls), (0, []))
        self.assertTrue(runtime._needs_completed_integrator_step)

    def test_factory_rejects_missing_dependencies_and_unsupported_interfaces(self):
        for factory, metadata, code in (
            (ModelExchangeNodeRuntimeFactory(None, _SolverFactory(_Solver())), _metadata(), ErrorCode.NOT_IMPLEMENTED),
            (ModelExchangeNodeRuntimeFactory(_SessionFactory(_Session()), None), _metadata(), ErrorCode.NOT_IMPLEMENTED),
            (ModelExchangeNodeRuntimeFactory(_SessionFactory(_Session()), _SolverFactory(_Solver())), _metadata(interface=InterfaceType.CO_SIMULATION), ErrorCode.UNSUPPORTED_INTERFACE),
            (ModelExchangeNodeRuntimeFactory(_SessionFactory(_Session()), _SolverFactory(_Solver())), _metadata(fmi_version="3.0"), ErrorCode.UNSUPPORTED_INTERFACE),
            (ModelExchangeNodeRuntimeFactory(_SessionFactory(_Session()), _SolverFactory(_Solver())), _metadata(can_execute=False), ErrorCode.UNSUPPORTED_INTERFACE),
        ):
            with self.subTest(code=code):
                with self.assertRaises(EngineError) as raised: factory.create(metadata, self._config())
                self.assertIs(raised.exception.code, code)

    def test_factory_closes_session_when_solver_creation_fails(self):
        session, solver = _Session(), _Solver()
        solver_factory = _SolverFactory(solver); solver_factory.error = RuntimeError("solver create failed")
        factory = ModelExchangeNodeRuntimeFactory(_SessionFactory(session), solver_factory)

        with self.assertRaisesRegex(RuntimeError, "solver create failed"):
            factory.create(_metadata(), self._config())

        self.assertEqual(session.close_calls, 1)

    def test_initialize_builds_problem_options_and_coordinator(self):
        session, solver = _Session(), _Solver()
        runtime = ModelExchangeNodeRuntime(session, solver, self._config(relative_tolerance=1e-6))
        runtime.initialize(); runtime.initialize()
        problem, options = solver.initialize_calls[0]
        self.assertIsInstance(problem, SessionModelExchangeProblem)
        self.assertEqual(options, SolverOptions(relative_tolerance=1e-6, maximum_step=None))
        self.assertEqual(session.initialize_calls, 1)

    def test_initialize_uses_default_tolerance_and_failure_states_remain_cleanable(self):
        session, solver = _Session(), _Solver()
        runtime = ModelExchangeNodeRuntime(session, solver, self._config())
        runtime.initialize()
        self.assertEqual(solver.initialize_calls[0][1].relative_tolerance, 1e-5)

        terminate_initialization = _Session(ModelExchangeInitialization(1, 0, terminate_requested=True))
        terminate_solver = _Solver()
        terminate_runtime = ModelExchangeNodeRuntime(terminate_initialization, terminate_solver, self._config())
        with self.assertRaises(EngineError) as raised: terminate_runtime.initialize()
        self.assertIs(raised.exception.code, ErrorCode.INITIALIZATION_ERROR)
        self.assertTrue(raised.exception.details["terminate_requested"])
        self.assertEqual(terminate_solver.initialize_calls, [])
        terminate_runtime.terminate(); terminate_runtime.close()
        self.assertEqual((terminate_initialization.terminate_calls, terminate_initialization.close_calls), (1, 1))

        failed_session, failed_solver = _Session(), _Solver(); failed_solver.initialize_error = RuntimeError("solver failed")
        failed_runtime = ModelExchangeNodeRuntime(failed_session, failed_solver, self._config())
        with self.assertRaises(EngineError) as failed: failed_runtime.initialize()
        self.assertIs(failed.exception.code, ErrorCode.INITIALIZATION_ERROR)
        failed_runtime.terminate(); failed_runtime.close()
        self.assertEqual((failed_session.terminate_calls, failed_session.close_calls), (1, 1))

    def test_set_inputs_uses_coordinator_bridge_and_empty_values_are_noop(self):
        session, solver = _Session(), _Solver()
        runtime = self._initialized(session, solver)
        runtime.set_inputs({})
        runtime.set_inputs({"u": 2.0})
        self.assertEqual(session.inputs, [{"u": 2.0}])
        self.assertEqual(session.events, ["event", "update", "continuous"])
        self.assertEqual(solver.integrate_calls, [])

        bridged = self._initialized(_Session(), _Solver())
        coordinator = _Coordinator()
        bridged._coordinator = coordinator
        bridged.set_inputs({"u": 3.0})
        self.assertEqual(coordinator.inputs, [{"u": 3.0}])
        self.assertEqual(bridged._session.inputs, [])

    def test_set_inputs_terminate_request_makes_runtime_terminal(self):
        runtime = self._initialized(_Session(), _Solver())
        runtime._coordinator = _Coordinator(input_terminate=True)
        with self.assertRaises(EngineError) as raised: runtime.set_inputs({"u": 2.0})
        self.assertIs(raised.exception.code, ErrorCode.STEP_ERROR)
        self.assertEqual(raised.exception.details["phase"], "routed_input_event")
        with self.assertRaises(EngineError) as lifecycle: runtime.set_inputs({"u": 3.0})
        self.assertIs(lifecycle.exception.code, ErrorCode.INPUT_SET_ERROR)
        for operation, code in ((lambda: runtime.advance_to(.01), ErrorCode.STEP_ERROR), (runtime.read_outputs, ErrorCode.OUTPUT_READ_ERROR)):
            with self.subTest(code=code):
                with self.assertRaises(EngineError) as lifecycle: operation()
                self.assertIs(lifecycle.exception.code, code)

    def test_advance_to_delegates_and_validates_outcomes_and_targets(self):
        runtime = self._initialized(_Session(), _Solver())
        coordinator = _Coordinator(outcome=ModelExchangeCheckpointOutcome(.01, True))
        runtime._coordinator = coordinator
        runtime.advance_to(.01)
        self.assertEqual(coordinator.targets, [.01])

        for outcome in (
            ModelExchangeCheckpointOutcome(.005, False),
            ModelExchangeCheckpointOutcome(.005, True),
            ModelExchangeCheckpointOutcome(.005, False, terminate_requested=True),
            ModelExchangeCheckpointOutcome(.005, False, stop_requested=True),
        ):
            with self.subTest(outcome=outcome):
                candidate = self._initialized(_Session(), _Solver())
                candidate._coordinator = _Coordinator(outcome=outcome)
                with self.assertRaises(EngineError) as raised: candidate.advance_to(.01)
                self.assertIs(raised.exception.code, ErrorCode.STEP_ERROR)

        current = self._initialized(_Session(), _Solver()); current._coordinator = _Coordinator(current_time=.01)
        current.advance_to(.01)
        for target in (0.0, float("nan"), float("inf"), .03):
            with self.subTest(target=target):
                with self.assertRaises(EngineError) as raised: current.advance_to(target)
                self.assertIs(raised.exception.code, ErrorCode.STEP_ERROR)

    def test_outputs_lifecycle_and_termination_are_stable(self):
        session, solver = _Session(), _Solver()
        runtime = ModelExchangeNodeRuntime(session, solver, self._config())
        for method, code in ((lambda: runtime.set_inputs({"u": 1.0}), ErrorCode.INPUT_SET_ERROR), (lambda: runtime.advance_to(.01), ErrorCode.STEP_ERROR), (runtime.read_outputs, ErrorCode.OUTPUT_READ_ERROR)):
            with self.subTest(code=code):
                with self.assertRaises(EngineError) as raised: method()
                self.assertIs(raised.exception.code, code)
        runtime.initialize(); self.assertEqual(runtime.read_outputs(), {"y": 1.0})
        error = EngineError(ErrorCode.TERMINATION_ERROR, "terminate failed")
        session.terminate_error = error
        with self.assertRaises(EngineError) as raised: runtime.terminate()
        self.assertIs(raised.exception, error)
        runtime.terminate()
        self.assertEqual(session.terminate_calls, 1)
        with self.assertRaises(EngineError) as lifecycle: runtime.advance_to(.01)
        self.assertIs(lifecycle.exception.code, ErrorCode.STEP_ERROR)

    def test_close_attempts_solver_and_session_once_and_keeps_all_failures(self):
        session, solver = _Session(), _Solver()
        runtime = ModelExchangeNodeRuntime(session, solver, self._config())
        solver.close_error = RuntimeError("solver close")
        session.close_error = RuntimeError("session close")
        with self.assertRaises(EngineError) as raised: runtime.close()
        self.assertIs(raised.exception.code, ErrorCode.CLEANUP_ERROR)
        self.assertEqual({item["component"] for item in raised.exception.details["cleanup_failures"]}, {"solver", "session"})
        runtime.close()
        self.assertEqual((solver.close_calls, session.close_calls), (1, 1))

    @staticmethod
    def _config(**overrides):
        values = {"stop_time": .02, "communication_step": .01, "execution_interface": InterfaceType.MODEL_EXCHANGE}
        values.update(overrides)
        return SimulationConfig(**values)

    @classmethod
    def _initialized(cls, session, solver):
        runtime = ModelExchangeNodeRuntime(session, solver, cls._config())
        runtime.initialize()
        return runtime
