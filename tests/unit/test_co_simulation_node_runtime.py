import math
import unittest

from farcel.application.node_runtime import (
    CoSimulationNodeRuntime,
    CoSimulationNodeRuntimeFactory,
)
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
    StepStatus,
)


def _metadata(
    *,
    interface: InterfaceType = InterfaceType.CO_SIMULATION,
    can_execute: bool = True,
) -> ModelMetadata:
    return ModelMetadata(
        model_id="node-runtime",
        source_path="node-runtime.fmu",
        fmi_version="3.0",
        model_name="NodeRuntime",
        interface_types=(interface,),
        executable_interface=interface if can_execute else None,
        capabilities=CapabilitySet(can_execute=can_execute),
        interface_capabilities=(InterfaceCapability(interface, can_execute=can_execute),),
    )


class _Session:
    def __init__(self, results: list[StepResult] | None = None) -> None:
        self.results = list(results or ())
        self.initialize_calls = 0
        self.step_calls: list[tuple[float, float]] = []
        self.inputs: list[dict[str, object]] = []
        self.terminate_calls = 0
        self.close_calls = 0
        self.initialize_error: BaseException | None = None
        self.terminate_error: BaseException | None = None
        self.outputs = {"y": 1.0}

    def initialize(self) -> None:
        self.initialize_calls += 1
        if self.initialize_error is not None:
            raise self.initialize_error

    def set_inputs(self, values) -> None:
        self.inputs.append(dict(values))

    def step(self, current_time: float, step_size: float) -> StepResult:
        self.step_calls.append((current_time, step_size))
        if self.results:
            return self.results.pop(0)
        reached_time = current_time + step_size
        return StepResult(reached_time, reached_time, step_size)

    def read_outputs(self):
        return self.outputs

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.terminate_error is not None:
            raise self.terminate_error

    def close(self) -> None:
        self.close_calls += 1


class _SessionFactory:
    def __init__(self, session: _Session) -> None:
        self.session = session
        self.calls: list[tuple[ModelMetadata, SimulationConfig]] = []

    def create(self, metadata: ModelMetadata, config: SimulationConfig) -> _Session:
        self.calls.append((metadata, config))
        return self.session


class CoSimulationNodeRuntimeTests(unittest.TestCase):
    def test_factory_creates_without_initializing(self) -> None:
        session = _Session()
        session_factory = _SessionFactory(session)
        metadata = _metadata()
        config = self._config()

        runtime = CoSimulationNodeRuntimeFactory(session_factory).create(metadata, config)

        self.assertIsInstance(runtime, CoSimulationNodeRuntime)
        self.assertEqual(session_factory.calls, [(metadata, config)])
        self.assertEqual(session.initialize_calls, 0)

    def test_factory_requires_available_executable_co_simulation(self) -> None:
        session = _Session()
        for factory, metadata, code in (
            (CoSimulationNodeRuntimeFactory(None), _metadata(), ErrorCode.NOT_IMPLEMENTED),
            (CoSimulationNodeRuntimeFactory(_SessionFactory(session)), _metadata(interface=InterfaceType.MODEL_EXCHANGE), ErrorCode.UNSUPPORTED_INTERFACE),
            (CoSimulationNodeRuntimeFactory(_SessionFactory(session)), _metadata(can_execute=False), ErrorCode.UNSUPPORTED_INTERFACE),
        ):
            with self.subTest(code=code):
                with self.assertRaises(EngineError) as raised:
                    factory.create(metadata, self._config())
                self.assertIs(raised.exception.code, code)

    def test_initialize_delegates_once_and_failure_still_allows_close(self) -> None:
        session = _Session()
        runtime = self._initialized(session)
        runtime.initialize()
        self.assertEqual(session.initialize_calls, 1)

        failed = _Session()
        failed.initialize_error = EngineError(ErrorCode.INITIALIZATION_ERROR, "init failed")
        failed_runtime = CoSimulationNodeRuntime(failed, self._config())
        with self.assertRaises(EngineError):
            failed_runtime.initialize()
        failed_runtime.terminate()
        failed_runtime.close()
        self.assertEqual((failed.terminate_calls, failed.close_calls), (0, 1))

    def test_read_outputs_and_set_inputs_delegate_after_initialization(self) -> None:
        session = _Session()
        runtime = self._initialized(session)

        self.assertEqual(runtime.read_outputs(), {"y": 1.0})
        runtime.set_inputs({"u": 2.0})
        runtime.set_inputs({})
        self.assertEqual(session.inputs, [{"u": 2.0}])

    def test_input_and_output_lifecycle_errors_are_stable(self) -> None:
        runtime = CoSimulationNodeRuntime(_Session(), self._config())
        with self.assertRaises(EngineError) as input_error:
            runtime.set_inputs({"u": 1.0})
        with self.assertRaises(EngineError) as output_error:
            runtime.read_outputs()
        self.assertIs(input_error.exception.code, ErrorCode.INPUT_SET_ERROR)
        self.assertIs(output_error.exception.code, ErrorCode.OUTPUT_READ_ERROR)

    def test_advance_to_uses_current_time_and_noops_at_same_checkpoint(self) -> None:
        session = _Session()
        runtime = self._initialized(session)

        runtime.advance_to(0.01)
        runtime.advance_to(0.01)
        runtime.advance_to(0.02)

        self.assertEqual(session.step_calls, [(0.0, 0.01), (0.01, 0.01)])

    def test_advance_to_rejects_invalid_past_and_beyond_stop_targets(self) -> None:
        runtime = self._initialized(_Session())
        runtime.advance_to(0.01)
        for target in (-0.1, float("nan"), float("inf"), 0.03):
            with self.subTest(target=target):
                with self.assertRaises(EngineError) as raised:
                    runtime.advance_to(target)
                self.assertIs(raised.exception.code, ErrorCode.STEP_ERROR)

    def test_advance_to_requires_initialized_live_runtime(self) -> None:
        runtime = CoSimulationNodeRuntime(_Session(), self._config())
        with self.assertRaises(EngineError) as raised:
            runtime.advance_to(0.01)
        self.assertIs(raised.exception.code, ErrorCode.STEP_ERROR)
        runtime.close()
        with self.assertRaises(EngineError):
            runtime.advance_to(0.01)

    def test_early_return_is_absorbed_until_target(self) -> None:
        session = _Session(
            [
                StepResult(0.01, 0.004, 0.01, early_return=True),
                StepResult(0.01, 0.01, 0.006),
            ]
        )
        runtime = self._initialized(session)

        runtime.advance_to(0.01)

        self.assertEqual(session.step_calls, [(0.0, 0.01), (0.004, 0.006)])

    def test_early_return_at_target_is_success(self) -> None:
        session = _Session([StepResult(0.01, 0.01, 0.01, early_return=True)])
        runtime = self._initialized(session)

        runtime.advance_to(0.01)

        self.assertEqual(len(session.step_calls), 1)

    def test_step_result_sanity_failures_are_step_errors(self) -> None:
        cases = (
            StepResult(0.01, 0.01, 0.01, status=StepStatus.FAILED),
            StepResult(0.01, 0.0, 0.01, early_return=True),
            StepResult(0.01, math.nan, 0.01, early_return=True),
            StepResult(0.01, 0.02, 0.01),
            StepResult(0.01, 0.004, 0.01),
        )
        for result in cases:
            with self.subTest(result=result):
                runtime = self._initialized(_Session([result]))
                with self.assertRaises(EngineError) as raised:
                    runtime.advance_to(0.01)
                self.assertIs(raised.exception.code, ErrorCode.STEP_ERROR)
                self.assertIn("target_time", raised.exception.details)

    def test_fragmenting_early_returns_hit_per_target_attempt_guard(self) -> None:
        session = _Session(
            [
                StepResult(0.01, 0.001, 0.01, early_return=True),
                StepResult(0.01, 0.002, 0.009, early_return=True),
            ]
        )
        runtime = CoSimulationNodeRuntime(session, self._config(), step_attempt_limit=2)
        runtime.initialize()

        with self.assertRaises(EngineError) as raised:
            runtime.advance_to(0.01)
        self.assertIs(raised.exception.code, ErrorCode.STEP_ERROR)
        self.assertEqual(raised.exception.details["step_attempt_count"], 3)
        self.assertEqual(len(session.step_calls), 2)

    def test_schedule_consumes_each_checkpoint_once_across_early_return(self) -> None:
        session = _Session(
            [
                StepResult(0.01, 0.004, 0.01, early_return=True),
                StepResult(0.01, 0.01, 0.006),
                StepResult(0.02, 0.02, 0.01),
            ]
        )
        config = self._config(
            input_schedule=(
                InputUpdate(0.0, {"u": 1.0}),
                InputUpdate(0.01, {"u": 2.0}),
            )
        )
        runtime = self._initialized(session, config)

        runtime.advance_to(0.01)
        self.assertEqual(session.inputs, [{"u": 1.0}])
        runtime.advance_to(0.02)
        self.assertEqual(session.inputs, [{"u": 1.0}, {"u": 2.0}])

    def test_missed_schedule_checkpoint_is_step_error(self) -> None:
        session = _Session()
        runtime = self._initialized(
            session,
            self._config(input_schedule=(InputUpdate(0.0, {"u": 1.0}),)),
        )
        runtime.advance_to(0.01)
        delayed = self._initialized(
            _Session(),
            self._config(input_schedule=(InputUpdate(0.0, {"u": 1.0}),)),
        )
        delayed._current_time = 0.01
        with self.assertRaises(EngineError) as raised:
            delayed.advance_to(0.02)
        self.assertIs(raised.exception.code, ErrorCode.STEP_ERROR)
        self.assertEqual(raised.exception.details["scheduled_input_time"], 0.0)

    def test_terminate_and_close_are_idempotent_and_close_survives_terminate_error(self) -> None:
        session = _Session()
        runtime = self._initialized(session)
        runtime.terminate()
        runtime.terminate()
        runtime.close()
        runtime.close()
        self.assertEqual((session.terminate_calls, session.close_calls), (1, 1))

        failed = _Session()
        failed.terminate_error = EngineError(ErrorCode.TERMINATION_ERROR, "terminate failed")
        failed_runtime = self._initialized(failed)
        with self.assertRaises(EngineError):
            failed_runtime.terminate()
        failed_runtime.close()
        self.assertEqual(failed.close_calls, 1)

    @staticmethod
    def _config(**overrides) -> SimulationConfig:
        values = {"stop_time": 0.02, "communication_step": 0.01}
        values.update(overrides)
        return SimulationConfig(**values)

    @staticmethod
    def _initialized(session: _Session, config: SimulationConfig | None = None) -> CoSimulationNodeRuntime:
        runtime = CoSimulationNodeRuntime(session, config or CoSimulationNodeRuntimeTests._config())
        runtime.initialize()
        return runtime
