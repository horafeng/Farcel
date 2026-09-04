from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Protocol

from farcel.application.model_exchange_problem import SessionModelExchangeProblem
from farcel.application.model_exchange_runtime import (
    ModelExchangeCheckpointCoordinator,
)
from farcel.application.validation import resolve_execution_interface
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import (
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    SolverOptions,
    StepResult,
    StepStatus,
)
from farcel.contracts.ports import (
    ModelExchangeSession,
    ModelExchangeSessionFactory,
    SessionFactory,
    SimulationSession,
    SolverAdapter,
    SolverFactory,
)


_DEFAULT_STEP_ATTEMPT_LIMIT = 10000


class ModelNodeRuntime(Protocol):
    """Application-internal lifecycle boundary for a future graph node."""

    def initialize(self) -> None: ...

    def set_inputs(self, values: Mapping[str, Any]) -> None: ...

    def advance_to(self, target_time: float) -> None: ...

    def read_outputs(self) -> Mapping[str, Any]: ...

    def terminate(self) -> None: ...

    def close(self) -> None: ...


class CoSimulationNodeRuntime:
    """Advance one Co-Simulation session to an exact outer checkpoint."""

    def __init__(
        self,
        session: SimulationSession,
        config: SimulationConfig,
        *,
        step_attempt_limit: int = _DEFAULT_STEP_ATTEMPT_LIMIT,
    ) -> None:
        self._session = session
        self._config = config
        self._step_attempt_limit = step_attempt_limit
        self._current_time = config.start_time
        self._next_input_update = 0
        self._initialized = False
        self._terminated = False
        self._closed = False
        self._tolerance = max(1e-12, config.communication_step * 1e-9)

    def initialize(self) -> None:
        if self._closed:
            raise EngineError(ErrorCode.INITIALIZATION_ERROR, "Node runtime 已关闭")
        if self._initialized:
            return
        self._session.initialize()
        self._initialized = True

    def set_inputs(self, values: Mapping[str, Any]) -> None:
        self._ensure_input_lifecycle()
        if not values:
            return
        self._session.set_inputs(values)

    def advance_to(self, target_time: float) -> None:
        self._ensure_step_lifecycle()
        if not isinstance(target_time, (int, float)) or isinstance(target_time, bool) or not math.isfinite(target_time):
            raise EngineError(ErrorCode.STEP_ERROR, "target_time 必须是有限数值")
        if target_time < self._current_time - self._tolerance:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "target_time 不能早于当前 node time",
                {"current_time": self._current_time, "target_time": target_time},
            )
        if target_time > self._config.stop_time + self._tolerance:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "target_time 不能超过配置 stop_time",
                {"target_time": target_time, "stop_time": self._config.stop_time},
            )
        if math.isclose(
            target_time, self._current_time, rel_tol=0.0, abs_tol=self._tolerance
        ):
            return

        self._apply_scheduled_input_at_current_time()
        step_attempt_count = 0
        while not math.isclose(
            self._current_time, target_time, rel_tol=0.0, abs_tol=self._tolerance
        ):
            step_attempt_count += 1
            if step_attempt_count > self._step_attempt_limit:
                raise EngineError(
                    ErrorCode.STEP_ERROR,
                    "FMU Early Return 在 node target 前超过重试上限",
                    {
                        "target_time": target_time,
                        "step_attempt_count": step_attempt_count,
                    },
                )
            call_time = self._current_time
            result = self._session.step(call_time, target_time - call_time)
            self._validate_step_result(result, call_time, target_time)
            self._current_time = result.reached_time

    def read_outputs(self) -> Mapping[str, Any]:
        if not self._initialized or self._terminated or self._closed:
            raise EngineError(ErrorCode.OUTPUT_READ_ERROR, "Node runtime 状态不允许读取 outputs")
        return self._session.read_outputs()

    def terminate(self) -> None:
        if self._terminated or self._closed or not self._initialized:
            return
        self._terminated = True
        self._session.terminate()

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._session.close()
        finally:
            self._closed = True

    def _ensure_input_lifecycle(self) -> None:
        if not self._initialized or self._terminated or self._closed:
            raise EngineError(ErrorCode.INPUT_SET_ERROR, "Node runtime 状态不允许设置 inputs")

    def _ensure_step_lifecycle(self) -> None:
        if not self._initialized or self._terminated or self._closed:
            raise EngineError(ErrorCode.STEP_ERROR, "Node runtime 状态不允许推进")

    def _apply_scheduled_input_at_current_time(self) -> None:
        if self._next_input_update >= len(self._config.input_schedule):
            return
        update = self._config.input_schedule[self._next_input_update]
        if update.time < self._current_time - self._tolerance:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "scheduled input checkpoint 已错过",
                {
                    "current_time": self._current_time,
                    "scheduled_input_time": update.time,
                },
            )
        if math.isclose(
            update.time, self._current_time, rel_tol=0.0, abs_tol=self._tolerance
        ):
            if update.values:
                self._session.set_inputs(update.values)
            self._next_input_update += 1

    def _validate_step_result(
        self,
        result: StepResult,
        current_time: float,
        target_time: float,
    ) -> None:
        details = {
            "current_time": current_time,
            "target_time": target_time,
            "requested_time": result.requested_time,
            "reached_time": result.reached_time,
            "early_return": result.early_return,
        }
        if result.status is not StepStatus.SUCCESS:
            raise EngineError(ErrorCode.STEP_ERROR, "FMU step 未成功完成", details)
        if not math.isfinite(result.reached_time) or result.reached_time <= current_time:
            raise EngineError(ErrorCode.STEP_ERROR, "FMU step 未取得单调进展", details)
        if result.reached_time > target_time + self._tolerance:
            raise EngineError(ErrorCode.STEP_ERROR, "FMU step 超过 node target", details)
        if not result.early_return and result.reached_time < target_time - self._tolerance:
            raise EngineError(ErrorCode.STEP_ERROR, "FMU step 未完整到达 node target", details)


class CoSimulationNodeRuntimeFactory:
    """Create uninitialized Co-Simulation node runtimes through Farcel ports."""

    def __init__(self, session_factory: SessionFactory | None) -> None:
        self._session_factory = session_factory

    def create(
        self,
        metadata: ModelMetadata,
        config: SimulationConfig,
    ) -> CoSimulationNodeRuntime:
        if self._session_factory is None:
            raise EngineError(ErrorCode.NOT_IMPLEMENTED, "未配置 Co-Simulation Session 实现")
        if (
            resolve_execution_interface(metadata, config) is not InterfaceType.CO_SIMULATION
            or not _can_execute_co_simulation(metadata)
        ):
            raise EngineError(
                ErrorCode.UNSUPPORTED_INTERFACE,
                "CoSimulationNodeRuntimeFactory 仅支持可执行 Co-Simulation",
            )
        return CoSimulationNodeRuntime(self._session_factory.create(metadata, config), config)


def _can_execute_co_simulation(metadata: ModelMetadata) -> bool:
    capability = next(
        (
            item
            for item in metadata.interface_capabilities
            if item.interface_type is InterfaceType.CO_SIMULATION
        ),
        None,
    )
    if capability is not None:
        return capability.can_execute
    return (
        InterfaceType.CO_SIMULATION in metadata.interface_types
        and metadata.capabilities.can_execute
        and metadata.executable_interface is InterfaceType.CO_SIMULATION
    )


class ModelExchangeNodeRuntime:
    """Application-internal checkpoint runtime for one FMI2 Model Exchange FMU."""

    def __init__(
        self,
        session: ModelExchangeSession,
        solver: SolverAdapter,
        config: SimulationConfig,
        *,
        needs_completed_integrator_step: bool = False,
    ) -> None:
        self._session = session
        self._solver = solver
        self._config = config
        self._needs_completed_integrator_step = needs_completed_integrator_step
        self._coordinator: ModelExchangeCheckpointCoordinator | None = None
        self._session_initialized = False
        self._ready = False
        self._terminal = False
        self._termination_attempted = False
        self._solver_close_attempted = False
        self._session_close_attempted = False
        self._closed = False
        self._tolerance = max(1e-12, config.communication_step * 1e-9)

    def initialize(self) -> None:
        if self._closed:
            raise EngineError(ErrorCode.INITIALIZATION_ERROR, "Node runtime 已关闭")
        if self._ready:
            return
        if self._session_initialized:
            raise EngineError(ErrorCode.INITIALIZATION_ERROR, "Node runtime 初始化未完成")

        initialization = self._session.initialize()
        self._session_initialized = True
        if initialization.terminate_requested:
            self._terminal = True
            raise EngineError(
                ErrorCode.INITIALIZATION_ERROR,
                "Model Exchange 初始化期间请求终止",
                {"terminate_requested": True, "current_time": self._config.start_time},
            )
        try:
            self._solver.initialize(
                SessionModelExchangeProblem(self._session),
                SolverOptions(
                    relative_tolerance=(
                        self._config.relative_tolerance
                        if self._config.relative_tolerance is not None
                        else 1e-5
                    ),
                    maximum_step=None,
                ),
            )
        except EngineError:
            raise
        except Exception as exc:
            raise EngineError(
                ErrorCode.INITIALIZATION_ERROR,
                "Model Exchange solver 初始化失败",
                {"diagnostic": str(exc)},
            ) from None
        self._coordinator = ModelExchangeCheckpointCoordinator(
            self._session,
            self._solver,
            self._config,
            initialization,
            needs_completed_integrator_step=self._needs_completed_integrator_step,
        )
        self._ready = True

    def set_inputs(self, values: Mapping[str, Any]) -> None:
        self._ensure_input_lifecycle()
        if not values:
            return
        assert self._coordinator is not None
        if self._coordinator.apply_inputs(values):
            self._terminal = True
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "Model Exchange input event 请求终止",
                {
                    "terminate_requested": True,
                    "phase": "routed_input_event",
                    "current_time": self._coordinator.current_time,
                },
            )

    def advance_to(self, target_time: float) -> None:
        self._ensure_step_lifecycle()
        if (
            not isinstance(target_time, (int, float))
            or isinstance(target_time, bool)
            or not math.isfinite(target_time)
        ):
            raise EngineError(ErrorCode.STEP_ERROR, "target_time 必须是有限数值")
        assert self._coordinator is not None
        current_time = self._coordinator.current_time
        if target_time < current_time - self._tolerance:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "target_time 不能早于当前 node time",
                {"current_time": current_time, "target_time": target_time},
            )
        if target_time > self._config.stop_time + self._tolerance:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "target_time 不能超过配置 stop_time",
                {"target_time": target_time, "stop_time": self._config.stop_time},
            )
        if math.isclose(target_time, current_time, rel_tol=0.0, abs_tol=self._tolerance):
            return

        outcome = self._coordinator.advance_to(target_time)
        if outcome.terminate_requested:
            self._terminal = True
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "Model Exchange checkpoint 推进请求终止",
                {
                    "terminate_requested": True,
                    "phase": "advance_to",
                    "current_time": outcome.reached_time,
                    "target_time": target_time,
                },
            )
        if outcome.stop_requested:
            self._terminal = True
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "Model Exchange node runtime 不支持 stop outcome",
                {"diagnostic": "unexpected stop_requested", "target_time": target_time},
            )
        if (
            not outcome.checkpoint_reached
            or not math.isclose(
                outcome.reached_time,
                target_time,
                rel_tol=0.0,
                abs_tol=self._tolerance,
            )
        ):
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "Model Exchange 未完整到达 node target",
                {
                    "current_time": current_time,
                    "target_time": target_time,
                    "reached_time": outcome.reached_time,
                    "checkpoint_reached": outcome.checkpoint_reached,
                },
            )

    def read_outputs(self) -> Mapping[str, Any]:
        if not self._ready or self._terminal or self._closed:
            raise EngineError(ErrorCode.OUTPUT_READ_ERROR, "Node runtime 状态不允许读取 outputs")
        return self._session.read_outputs()

    def terminate(self) -> None:
        if self._termination_attempted or self._closed or not self._session_initialized:
            return
        self._termination_attempted = True
        self._terminal = True
        self._session.terminate()

    def close(self) -> None:
        if self._closed:
            return
        failures: list[dict[str, str]] = []
        if not self._solver_close_attempted:
            self._solver_close_attempted = True
            try:
                self._solver.close()
            except Exception as exc:
                failures.append({"component": "solver", "diagnostic": str(exc)})
        if not self._session_close_attempted:
            self._session_close_attempted = True
            try:
                self._session.close()
            except Exception as exc:
                failures.append({"component": "session", "diagnostic": str(exc)})
        self._closed = True
        if failures:
            raise EngineError(
                ErrorCode.CLEANUP_ERROR,
                "Model Exchange node runtime 资源释放失败",
                {"cleanup_failures": tuple(failures)},
            )

    def _ensure_input_lifecycle(self) -> None:
        if not self._ready or self._terminal or self._closed:
            raise EngineError(ErrorCode.INPUT_SET_ERROR, "Node runtime 状态不允许设置 inputs")

    def _ensure_step_lifecycle(self) -> None:
        if not self._ready or self._terminal or self._closed:
            raise EngineError(ErrorCode.STEP_ERROR, "Node runtime 状态不允许推进")


class ModelExchangeNodeRuntimeFactory:
    """Create uninitialized FMI2 Model Exchange node runtimes through ports."""

    def __init__(
        self,
        session_factory: ModelExchangeSessionFactory | None,
        solver_factory: SolverFactory | None,
    ) -> None:
        self._session_factory = session_factory
        self._solver_factory = solver_factory

    def create(
        self,
        metadata: ModelMetadata,
        config: SimulationConfig,
    ) -> ModelExchangeNodeRuntime:
        if self._session_factory is None or self._solver_factory is None:
            raise EngineError(ErrorCode.NOT_IMPLEMENTED, "未配置 Model Exchange runtime 实现")
        capability = next(
            (
                item
                for item in metadata.interface_capabilities
                if item.interface_type is InterfaceType.MODEL_EXCHANGE
            ),
            None,
        )
        if (
            resolve_execution_interface(metadata, config) is not InterfaceType.MODEL_EXCHANGE
            or metadata.fmi_version != "2.0"
            or capability is None
            or not capability.can_execute
        ):
            raise EngineError(
                ErrorCode.UNSUPPORTED_INTERFACE,
                "ModelExchangeNodeRuntimeFactory 仅支持可执行 FMI 2.0 Model Exchange",
            )
        session = self._session_factory.create(metadata, config)
        try:
            solver = self._solver_factory.create()
        except Exception:
            try:
                session.close()
            except Exception:
                pass
            raise
        return ModelExchangeNodeRuntime(
            session,
            solver,
            config,
            needs_completed_integrator_step=capability.needs_completed_integrator_step,
        )
