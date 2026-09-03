from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from farcel.application.validation import resolve_output_interval
from farcel.application.model_exchange_problem import SessionModelExchangeProblem
from farcel.application.model_exchange_runtime import ModelExchangeCheckpointCoordinator
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import (
    InterfaceType,
    ModelMetadata,
    ResultChunk,
    RunProgress,
    SimulationConfig,
    SimulationResult,
    SimulationState,
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
from farcel.contracts.run_control import RunControl


_MAX_STEP_ATTEMPTS_PER_COMMUNICATION_TARGET = 10000


class ExecutionRunner(Protocol):
    """Application-internal execution boundary selected by ``FarcelEngine``."""

    def run(
        self,
        path: str | Path,
        metadata: ModelMetadata,
        config: SimulationConfig,
        *,
        control: RunControl | None = None,
        on_progress: Callable[[RunProgress], None] | None = None,
        on_result_chunk: Callable[[ResultChunk], None] | None = None,
        result_chunk_size: int = 256,
        step_attempt_limit: int = _MAX_STEP_ATTEMPTS_PER_COMMUNICATION_TARGET,
    ) -> SimulationResult: ...


class CoSimulationRunner:
    """Run the existing FMI Co-Simulation lifecycle through Farcel ports only."""

    def __init__(self, session_factory: SessionFactory | None) -> None:
        self._session_factory = session_factory

    def run(
        self,
        path: str | Path,
        metadata: ModelMetadata,
        config: SimulationConfig,
        *,
        control: RunControl | None = None,
        on_progress: Callable[[RunProgress], None] | None = None,
        on_result_chunk: Callable[[ResultChunk], None] | None = None,
        result_chunk_size: int = 256,
        step_attempt_limit: int = _MAX_STEP_ATTEMPTS_PER_COMMUNICATION_TARGET,
    ) -> SimulationResult:
        session: SimulationSession | None = None
        state = SimulationState.CREATED
        current_time = config.start_time
        next_input_update = 0
        primary_error: EngineError | None = None
        base_error: BaseException | None = None
        simulation_result: SimulationResult | None = None

        try:
            if self._session_factory is None:
                raise EngineError(ErrorCode.NOT_IMPLEMENTED, "未配置仿真 Session 实现")

            session = self._session_factory.create(metadata, config)
            session.initialize()
            state = SimulationState.READY

            completed_steps = 0
            result_accumulator = _ResultAccumulator(
                config.selected_outputs, on_result_chunk, result_chunk_size
            )
            tolerance = max(1e-12, config.communication_step * 1e-9)
            output_interval = resolve_output_interval(config)
            _record_sample(
                result_accumulator,
                current_time,
                session.read_outputs() if config.selected_outputs else {},
            )
            supports_variable_step = _supports_variable_step(metadata)
            _notify_progress(
                on_progress,
                config,
                current_time,
                completed_steps,
                result_accumulator.sample_count,
                SimulationState.RUNNING,
            )
            stopped = False
            step_attempts_for_target = 0

            while current_time < config.stop_time and not math.isclose(
                current_time, config.stop_time, rel_tol=0.0, abs_tol=tolerance
            ):
                if control is not None and control.stop_requested:
                    stopped = True
                    break
                configured_target = config.start_time + (
                    completed_steps + 1
                ) * config.communication_step
                communication_target = min(configured_target, config.stop_time)
                if (
                    communication_target < configured_target
                    and not supports_variable_step
                ):
                    break
                step_attempts_for_target += 1
                if step_attempts_for_target > step_attempt_limit:
                    raise EngineError(
                        ErrorCode.STEP_ERROR,
                        "FMU Early Return 在通信目标前超过重试上限",
                        {
                            "communication_target": communication_target,
                            "step_attempt_count": step_attempts_for_target,
                        },
                    )
                step_size = communication_target - current_time
                next_input_update = self._apply_scheduled_inputs(
                    session, config, next_input_update, current_time
                )
                result = self._step(session, current_time, step_size)
                current_time = result.reached_time
                state = SimulationState.RUNNING
                reached_communication_target = math.isclose(
                    current_time,
                    communication_target,
                    rel_tol=0.0,
                    abs_tol=tolerance,
                )
                if (
                    result.early_return
                    and current_time > communication_target
                    and not reached_communication_target
                ):
                    raise EngineError(
                        ErrorCode.STEP_ERROR,
                        "FMI 3 Early Return 超过配置通信目标",
                        {
                            "current_time": current_time,
                            "communication_target": communication_target,
                            "early_return": True,
                        },
                    )

                if reached_communication_target or not result.early_return:
                    completed_steps += 1
                    step_attempts_for_target = 0
                    if _is_output_sample_time(
                        current_time,
                        config.start_time,
                        output_interval,
                        tolerance,
                    ):
                        _record_sample(
                            result_accumulator,
                            current_time,
                            session.read_outputs() if config.selected_outputs else {},
                        )
                _notify_progress(
                    on_progress,
                    config,
                    current_time,
                    completed_steps,
                    result_accumulator.sample_count,
                    SimulationState.RUNNING,
                )

            if not math.isclose(
                result_accumulator.final_time,
                current_time,
                rel_tol=0.0,
                abs_tol=tolerance,
            ):
                _record_sample(
                    result_accumulator,
                    current_time,
                    session.read_outputs() if config.selected_outputs else {},
                )

            completion_state = (
                SimulationState.STOPPED if stopped else SimulationState.COMPLETED
            )
            simulation_result = SimulationResult(
                fmu_path=str(Path(path).expanduser().resolve()),
                start_time=config.start_time,
                stop_time=config.stop_time,
                step_size=config.communication_step,
                completed_steps=completed_steps,
                final_time=current_time,
                completion_state=completion_state,
                timestamps=result_accumulator.timestamps,
                outputs=result_accumulator.outputs,
            )
            try:
                state = self._terminate(session, state)
            except BaseException:
                state = SimulationState.ERROR
                raise
            result_accumulator.flush_final()
            _notify_progress(
                on_progress,
                config,
                current_time,
                completed_steps,
                simulation_result.sample_count,
                completion_state,
            )
        except EngineError as exc:
            primary_error = exc
        except Exception as exc:
            primary_error = EngineError(
                ErrorCode.INTERNAL_ERROR,
                "仿真执行发生未预期错误",
                {"diagnostic": str(exc)},
            )
        except BaseException as exc:
            base_error = exc

        cleanup_errors: list[EngineError] = []
        if session is not None:
            if state in {SimulationState.READY, SimulationState.RUNNING}:
                try:
                    state = self._terminate(session, state)
                except EngineError as exc:
                    cleanup_errors.append(exc)
            try:
                session.close()
            except EngineError as exc:
                cleanup_errors.append(exc)

        cleanup_error = _combine_cleanup_errors(cleanup_errors)

        if base_error is not None:
            if cleanup_error is not None:
                raise cleanup_error from base_error
            raise base_error
        if primary_error is not None:
            if cleanup_error is not None:
                details = dict(primary_error.details)
                details["cleanup_error"] = {
                    "code": cleanup_error.code.value,
                    "message": cleanup_error.message,
                    "details": dict(cleanup_error.details),
                }
                raise EngineError(primary_error.code, primary_error.message, details) from None
            raise primary_error from None
        if cleanup_error is not None:
            raise cleanup_error from None
        if simulation_result is None:
            raise EngineError(ErrorCode.INTERNAL_ERROR, "仿真未生成结果")
        return simulation_result

    @staticmethod
    def _apply_scheduled_inputs(
        session: SimulationSession,
        config: SimulationConfig,
        next_input_update: int,
        current_time: float,
    ) -> int:
        schedule = config.input_schedule
        if next_input_update >= len(schedule):
            return next_input_update
        update = schedule[next_input_update]
        tolerance = max(1e-12, config.communication_step * 1e-9)
        if math.isclose(update.time, current_time, rel_tol=0.0, abs_tol=tolerance):
            if update.values:
                session.set_inputs(update.values)
            return next_input_update + 1
        return next_input_update

    @staticmethod
    def _step(
        session: SimulationSession, current_time: float, step_size: float
    ) -> StepResult:
        result = session.step(current_time, step_size)
        if result.status is not StepStatus.SUCCESS:
            raise EngineError(ErrorCode.STEP_ERROR, "FMU step 未成功完成")
        if not math.isfinite(result.reached_time) or result.reached_time <= current_time:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMU step 未返回单调递增的 reached time",
                {
                    "current_time": current_time,
                    "requested_time": result.requested_time,
                    "reached_time": result.reached_time,
                    "early_return": result.early_return,
                },
            )
        return result

    @staticmethod
    def _terminate(
        session: SimulationSession, state: SimulationState
    ) -> SimulationState:
        if state is SimulationState.STOPPED:
            return state
        session.terminate()
        return SimulationState.STOPPED


class ModelExchangeRunner:
    """Run an internally composed FMI2 Model Exchange lifecycle through ports."""

    def __init__(
        self,
        session_factory: ModelExchangeSessionFactory | None,
        solver_factory: SolverFactory | None,
    ) -> None:
        self._session_factory = session_factory
        self._solver_factory = solver_factory

    def run(
        self,
        path: str | Path,
        metadata: ModelMetadata,
        config: SimulationConfig,
        *,
        control: RunControl | None = None,
        on_progress: Callable[[RunProgress], None] | None = None,
        on_result_chunk: Callable[[ResultChunk], None] | None = None,
        result_chunk_size: int = 256,
        step_attempt_limit: int = _MAX_STEP_ATTEMPTS_PER_COMMUNICATION_TARGET,
    ) -> SimulationResult:
        del step_attempt_limit
        session: ModelExchangeSession | None = None
        solver: SolverAdapter | None = None
        primary_error: EngineError | None = None
        base_error: BaseException | None = None
        simulation_result: SimulationResult | None = None
        result_accumulator: _ResultAccumulator | None = None

        try:
            if self._session_factory is None or self._solver_factory is None:
                raise EngineError(ErrorCode.NOT_IMPLEMENTED, "未配置 Model Exchange runtime 实现")

            session = self._session_factory.create(metadata, config)
            initialization = session.initialize()
            current_time = session.get_initial_time()
            completed_steps = 0
            tolerance = max(1e-12, config.communication_step * 1e-9)
            output_interval = resolve_output_interval(config)
            result_accumulator = _ResultAccumulator(
                config.selected_outputs, on_result_chunk, result_chunk_size
            )
            _record_sample(
                result_accumulator,
                current_time,
                session.read_outputs() if config.selected_outputs else {},
            )
            _notify_progress(
                on_progress,
                config,
                current_time,
                completed_steps,
                result_accumulator.sample_count,
                SimulationState.RUNNING,
            )

            stopped = False
            terminate_requested = initialization.terminate_requested
            if not terminate_requested:
                capability = next(
                    (
                        item
                        for item in metadata.interface_capabilities
                        if item.interface_type is InterfaceType.MODEL_EXCHANGE
                    ),
                    None,
                )
                if capability is None:
                    raise EngineError(
                        ErrorCode.UNSUPPORTED_INTERFACE,
                        "模型不包含 Model Exchange 接口 capability",
                    )
                solver = self._solver_factory.create()
                solver.initialize(
                    SessionModelExchangeProblem(session),
                    SolverOptions(
                        relative_tolerance=(
                            config.relative_tolerance
                            if config.relative_tolerance is not None
                            else 1e-5
                        ),
                        maximum_step=None,
                    ),
                )
                coordinator = ModelExchangeCheckpointCoordinator(
                    session,
                    solver,
                    config,
                    initialization,
                    needs_completed_integrator_step=(
                        capability.needs_completed_integrator_step
                    ),
                )
                while current_time < config.stop_time and not math.isclose(
                    current_time,
                    config.stop_time,
                    rel_tol=0.0,
                    abs_tol=tolerance,
                ):
                    if control is not None and control.stop_requested:
                        stopped = True
                        break
                    target = min(
                        config.start_time
                        + (completed_steps + 1) * config.communication_step,
                        config.stop_time,
                    )
                    outcome = coordinator.advance_to(
                        target,
                        should_stop=(
                            (lambda: control.stop_requested)
                            if control is not None
                            else None
                        ),
                    )
                    current_time = outcome.reached_time
                    if outcome.checkpoint_reached:
                        completed_steps += 1
                        if _is_output_sample_time(
                            current_time,
                            config.start_time,
                            output_interval,
                            tolerance,
                        ):
                            _record_sample(
                                result_accumulator,
                                current_time,
                                session.read_outputs()
                                if config.selected_outputs
                                else {},
                            )
                    _notify_progress(
                        on_progress,
                        config,
                        current_time,
                        completed_steps,
                        result_accumulator.sample_count,
                        SimulationState.RUNNING,
                    )
                    if outcome.terminate_requested:
                        terminate_requested = True
                        break
                    if outcome.stop_requested or (
                        control is not None and control.stop_requested
                    ):
                        stopped = True
                        break

            if not math.isclose(
                result_accumulator.final_time,
                current_time,
                rel_tol=0.0,
                abs_tol=tolerance,
            ):
                _record_sample(
                    result_accumulator,
                    current_time,
                    session.read_outputs() if config.selected_outputs else {},
                )
            simulation_result = SimulationResult(
                fmu_path=str(Path(path).expanduser().resolve()),
                start_time=config.start_time,
                stop_time=config.stop_time,
                step_size=config.communication_step,
                completed_steps=completed_steps,
                final_time=current_time,
                completion_state=(
                    SimulationState.STOPPED if stopped else SimulationState.COMPLETED
                ),
                timestamps=result_accumulator.timestamps,
                outputs=result_accumulator.outputs,
            )
        except EngineError as exc:
            primary_error = exc
        except Exception as exc:
            primary_error = EngineError(
                ErrorCode.INTERNAL_ERROR,
                "Model Exchange 执行发生未预期错误",
                {"diagnostic": str(exc)},
            )
        except BaseException as exc:
            base_error = exc

        cleanup_errors: list[EngineError] = []
        if session is not None:
            try:
                session.terminate()
            except BaseException as exc:
                cleanup_errors.append(_normalise_me_cleanup_error("session_terminate", exc))
        if solver is not None:
            try:
                solver.close()
            except BaseException as exc:
                cleanup_errors.append(_normalise_me_cleanup_error("solver", exc))
        if session is not None:
            try:
                session.close()
            except BaseException as exc:
                cleanup_errors.append(_normalise_me_cleanup_error("session", exc))

        cleanup_error = _combine_cleanup_errors(cleanup_errors)
        if base_error is not None:
            if cleanup_error is not None:
                raise cleanup_error from base_error
            raise base_error
        if primary_error is not None:
            if cleanup_error is not None:
                details = dict(primary_error.details)
                details["cleanup_error"] = _cleanup_error_details(cleanup_error)
                raise EngineError(primary_error.code, primary_error.message, details) from None
            raise primary_error from None
        if cleanup_error is not None:
            raise cleanup_error from None
        if simulation_result is None or result_accumulator is None:
            raise EngineError(ErrorCode.INTERNAL_ERROR, "Model Exchange 仿真未生成结果")
        result_accumulator.flush_final()
        _notify_progress(
            on_progress,
            config,
            simulation_result.final_time,
            simulation_result.completed_steps,
            simulation_result.sample_count,
            simulation_result.completion_state,
        )
        return simulation_result


def _supports_variable_step(metadata: ModelMetadata) -> bool:
    return any(
        capability.interface_type is InterfaceType.CO_SIMULATION
        and capability.can_handle_variable_step
        for capability in metadata.interface_capabilities
    )


def _is_output_sample_time(
    current_time: float,
    start_time: float,
    output_interval: float,
    tolerance: float,
) -> bool:
    sample_index = (current_time - start_time) / output_interval
    return sample_index > 0 and math.isclose(
        sample_index, round(sample_index), rel_tol=0.0, abs_tol=tolerance / output_interval
    )


class _ResultAccumulator:
    """Keep the canonical result and optionally deliver contiguous sample chunks."""

    def __init__(
        self,
        selected_outputs: tuple[str, ...],
        callback: Callable[[ResultChunk], None] | None,
        chunk_size: int,
    ) -> None:
        self._selected_outputs = selected_outputs
        self._callback = callback
        self._chunk_size = chunk_size
        self._run_id = str(uuid4())
        self._sequence = 0
        self._timestamps: list[float] = []
        self._outputs = {name: [] for name in selected_outputs}
        self._pending_time: list[float] = []
        self._pending_columns = {name: [] for name in selected_outputs}

    @property
    def sample_count(self) -> int:
        return len(self._timestamps)

    @property
    def final_time(self) -> float:
        return self._timestamps[-1]

    @property
    def timestamps(self) -> tuple[float, ...]:
        return tuple(self._timestamps)

    @property
    def outputs(self) -> dict[str, tuple[Any, ...]]:
        return {name: tuple(values) for name, values in self._outputs.items()}

    def record_sample(self, timestamp: float, values: Mapping[str, Any]) -> None:
        missing = tuple(name for name in self._selected_outputs if name not in values)
        if missing:
            raise EngineError(
                ErrorCode.OUTPUT_READ_ERROR,
                "Session 未返回全部所选输出变量",
                {"variables": missing},
            )
        if self._callback is not None and len(self._pending_time) == self._chunk_size:
            self._flush(final_chunk=False)

        self._timestamps.append(timestamp)
        for name in self._selected_outputs:
            value = values[name]
            self._outputs[name].append(value)
            if self._callback is not None:
                self._pending_columns[name].append(value)
        if self._callback is not None:
            self._pending_time.append(timestamp)

    def flush_final(self) -> None:
        if self._callback is not None:
            self._flush(final_chunk=True)

    def _flush(self, *, final_chunk: bool) -> None:
        if not self._pending_time:
            return
        chunk = ResultChunk(
            run_id=self._run_id,
            sequence=self._sequence,
            time=tuple(self._pending_time),
            columns={
                name: tuple(values) for name, values in self._pending_columns.items()
            },
            final_chunk=final_chunk,
        )
        try:
            self._callback(chunk)
        except Exception as exc:
            raise EngineError(
                ErrorCode.INTERNAL_ERROR,
                "结果分块回调执行失败",
                {"chunk_callback_diagnostic": str(exc)},
            ) from None
        self._sequence += 1
        self._pending_time.clear()
        for values in self._pending_columns.values():
            values.clear()


def _record_sample(
    accumulator: _ResultAccumulator, timestamp: float, values: Mapping[str, Any]
) -> None:
    accumulator.record_sample(timestamp, values)


def validate_result_chunk_size(result_chunk_size: int) -> None:
    if (
        isinstance(result_chunk_size, bool)
        or not isinstance(result_chunk_size, int)
        or result_chunk_size <= 0
    ):
        raise EngineError(
            ErrorCode.CONFIG_ERROR,
            "仿真配置验证失败",
            {
                "issues": (
                    {
                        "field": "result_chunk_size",
                        "code": "INVALID_RESULT_CHUNK_SIZE",
                        "message": "result_chunk_size 必须是大于 0 的整数",
                    },
                )
            },
        )


def _notify_progress(
    callback: Callable[[RunProgress], None] | None,
    config: SimulationConfig,
    current_time: float,
    completed_steps: int,
    sample_count: int,
    state: SimulationState,
) -> None:
    if callback is None:
        return
    fraction = (current_time - config.start_time) / (config.stop_time - config.start_time)
    progress = RunProgress(
        start_time=config.start_time,
        stop_time=config.stop_time,
        current_time=current_time,
        completed_steps=completed_steps,
        sample_count=sample_count,
        fraction=min(1.0, max(0.0, fraction)),
        state=state,
    )
    try:
        callback(progress)
    except Exception as exc:
        raise EngineError(
            ErrorCode.INTERNAL_ERROR,
            "运行进度回调执行失败",
            {"callback_diagnostic": str(exc)},
        ) from None


def _combine_cleanup_errors(errors: list[EngineError]) -> EngineError | None:
    if not errors:
        return None
    primary = errors[0]
    if len(errors) == 1:
        return primary
    details = dict(primary.details)
    details["additional_cleanup_errors"] = tuple(
        {
            "code": error.code.value,
            "message": error.message,
            "details": dict(error.details),
        }
        for error in errors[1:]
    )
    return EngineError(primary.code, primary.message, details)


def _normalise_me_cleanup_error(component: str, error: BaseException) -> EngineError:
    if isinstance(error, EngineError):
        return error
    return EngineError(
        ErrorCode.CLEANUP_ERROR,
        "Model Exchange runtime 资源释放失败",
        {"component": component, "diagnostic": str(error)},
    )


def _cleanup_error_details(error: EngineError) -> dict[str, object]:
    return {
        "code": error.code.value,
        "message": error.message,
        "details": dict(error.details),
    }
