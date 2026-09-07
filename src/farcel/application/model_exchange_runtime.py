from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any
from dataclasses import dataclass

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import (
    ModelExchangeInitialization, SimulationConfig, SolverAdvanceStatus,
    SolverResetReason,
)
from farcel.contracts.ports import ModelExchangeSession, SolverAdapter


_MAX_ME_DISCRETE_ITERATIONS = 1000
_MAX_EVENT_CYCLES_PER_CHECKPOINT = 1000


@dataclass(frozen=True, slots=True)
class ModelExchangeCheckpointOutcome:
    reached_time: float
    checkpoint_reached: bool
    terminate_requested: bool = False
    event_count: int = 0
    stop_requested: bool = False


class ModelExchangeCheckpointCoordinator:
    """Application-owned FMI2 ME event/checkpoint orchestration.

    This is intentionally not a runner: it creates no result, progress, chunk
    or stop semantics and is unreachable from public ``run_fmu()``.
    """

    def __init__(self, session: ModelExchangeSession, solver: SolverAdapter,
                 config: SimulationConfig, initialization: ModelExchangeInitialization,
                 *, needs_completed_integrator_step: bool = False) -> None:
        self._session, self._solver, self._config = session, solver, config
        self._current_time = config.start_time
        self._next_input_index = 0
        self._next_time_event = (initialization.next_event_time
            if initialization.next_event_time_defined else None)
        self._needs_completed_integrator_step = needs_completed_integrator_step
        self._tolerance = max(1e-12, config.communication_step * 1e-9)

    @property
    def current_time(self) -> float:
        return self._current_time

    def advance_to(
        self,
        checkpoint: float,
        should_stop: Callable[[], bool] | None = None,
    ) -> ModelExchangeCheckpointOutcome:
        if not math.isfinite(checkpoint) or checkpoint < self._current_time - self._tolerance:
            raise self._error("checkpoint 必须是当前时间之后的有限数值", checkpoint=checkpoint)
        event_cycles = event_count = 0
        while not self._close(self._current_time, checkpoint):
            if should_stop is not None and should_stop():
                return ModelExchangeCheckpointOutcome(
                    self._current_time, False, False, event_count, True
                )
            event_cycles += 1
            if event_cycles > _MAX_EVENT_CYCLES_PER_CHECKPOINT:
                raise self._error("事件循环超过上限", checkpoint=checkpoint, event_cycle_count=event_cycles)
            input_due = self._input_due()
            time_due = self._time_event_due()
            if input_due or time_due:
                terminated = self._handle_event(input_due=input_due, time_due=time_due,
                                                state_event=False, integrator_event=False)
                if terminated:
                    return ModelExchangeCheckpointOutcome(self._current_time, False, True, event_count + 1)
                event_count += 1
                if should_stop is not None and should_stop():
                    return ModelExchangeCheckpointOutcome(
                        self._current_time, False, False, event_count, True
                    )
                continue
            target = min(checkpoint, self._next_input_time(), self._next_time_event or math.inf)
            if should_stop is not None and should_stop():
                return ModelExchangeCheckpointOutcome(
                    self._current_time, False, False, event_count, True
                )
            result = self._solver.integrate_to(target)
            if result.status is SolverAdvanceStatus.FAILED:
                raise self._error("Model Exchange solver 推进失败", checkpoint=checkpoint,
                                  requested_target=target, reached_time=result.reached_time,
                                  solver_diagnostic=result.failure_message)
            if result.reached_time > target + self._tolerance or result.reached_time < self._current_time - self._tolerance:
                raise self._error("solver 返回非法 reached time", checkpoint=checkpoint,
                                  requested_target=target, reached_time=result.reached_time)
            advanced = result.reached_time > self._current_time + self._tolerance
            state_event = result.status is SolverAdvanceStatus.STATE_EVENT
            self._current_time = result.reached_time
            integrator_event = False
            terminate = False
            if advanced and self._needs_completed_integrator_step:
                step = self._session.completed_integrator_step()
                integrator_event, terminate = step.enter_event_mode, step.terminate_requested
            if terminate:
                return ModelExchangeCheckpointOutcome(self._current_time, False, True, event_count)
            input_due, time_due = self._input_due(), self._time_event_due()
            if state_event or input_due or time_due or integrator_event:
                terminated = self._handle_event(input_due=input_due, time_due=time_due,
                                                state_event=state_event, integrator_event=integrator_event)
                if terminated:
                    return ModelExchangeCheckpointOutcome(self._current_time, False, True, event_count + 1)
                event_count += 1
                if should_stop is not None and should_stop():
                    return ModelExchangeCheckpointOutcome(
                        self._current_time,
                        self._close(self._current_time, checkpoint),
                        False,
                        event_count,
                        True,
                    )
            elif not advanced:
                raise self._error("solver 未取得进展且没有可处理事件", checkpoint=checkpoint)
        return ModelExchangeCheckpointOutcome(self._current_time, True, False, event_count)

    def apply_inputs(self, values: Mapping[str, Any]) -> bool:
        """Apply routed inputs as one FMI input event at the current time.

        The existing event loop owns scheduled inputs and time events, so any
        event already due at this checkpoint is deliberately folded into this
        same Event Mode cycle.
        """
        if not values:
            return False
        return self._handle_event(
            input_due=self._input_due(),
            time_due=self._time_event_due(),
            state_event=False,
            integrator_event=False,
            external_inputs=values,
        )

    def _handle_event(self, *, input_due: bool, time_due: bool, state_event: bool,
                      integrator_event: bool,
                      external_inputs: Mapping[str, Any] | None = None) -> bool:
        if input_due:
            update = self._config.input_schedule[self._next_input_index]
            if update.values: self._session.set_inputs(update.values)
            self._next_input_index += 1
        if external_inputs:
            self._session.set_inputs(external_inputs)
        self._session.enter_event_mode()
        states_changed = nominals_changed = False
        final_update = None
        for count in range(1, _MAX_ME_DISCRETE_ITERATIONS + 1):
            update = self._session.update_discrete_states()
            states_changed |= update.continuous_states_changed
            nominals_changed |= update.nominals_changed
            final_update = update
            if update.terminate_requested:
                return True
            if not update.discrete_states_need_update:
                break
        else:
            raise self._error("运行时离散状态迭代超过上限", phase="runtime_discrete_state_iteration",
                              iteration_count=_MAX_ME_DISCRETE_ITERATIONS, current_time=self._current_time)
        self._next_time_event = final_update.next_event_time if final_update.next_event_time_defined else None
        if self._next_time_event is not None and self._next_time_event < self._current_time - self._tolerance:
            raise self._error("FMU 返回过去的 time event", current_time=self._current_time,
                              next_event_time=self._next_time_event)
        self._session.enter_continuous_time_mode()
        reason = (SolverResetReason.NOMINALS_CHANGED if nominals_changed else
                  SolverResetReason.CONTINUOUS_STATES_CHANGED if states_changed else
                  SolverResetReason.OTHER_PROBLEM_CHANGE if (input_due or external_inputs or state_event or integrator_event) else None)
        if reason is not None:
            self._solver.reset(self._current_time, reason)
        return False

    def _next_input_time(self) -> float:
        return (self._config.input_schedule[self._next_input_index].time
                if self._next_input_index < len(self._config.input_schedule) else math.inf)

    def _input_due(self) -> bool:
        return self._close(self._next_input_time(), self._current_time)

    def _time_event_due(self) -> bool:
        return self._next_time_event is not None and self._close(self._next_time_event, self._current_time)

    def _close(self, left: float, right: float) -> bool:
        return math.isclose(left, right, rel_tol=0.0, abs_tol=self._tolerance)

    @staticmethod
    def _error(message: str, **details: object) -> EngineError:
        return EngineError(ErrorCode.STEP_ERROR, message, details)
