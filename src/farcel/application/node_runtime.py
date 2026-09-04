from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Protocol

from farcel.application.validation import resolve_execution_interface
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import (
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    StepResult,
    StepStatus,
)
from farcel.contracts.ports import SessionFactory, SimulationSession


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
        self._session.terminate()
        self._terminated = True

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
