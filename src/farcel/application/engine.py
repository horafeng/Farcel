from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from farcel.application.validation import resolve_output_interval, validate_config
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import (
    InterfaceType,
    ModelMetadata,
    ExportReport,
    ResultChunk,
    RunProgress,
    SessionHandle,
    SimulationConfig,
    SimulationResult,
    SimulationState,
    StepResult,
    StepStatus,
    ValidationReport,
)
from farcel.contracts.run_control import RunControl
from farcel.contracts.ports import (
    ModelImporter,
    ResultExporter,
    SessionFactory,
    SimulationSession,
)


@dataclass(slots=True)
class _SessionRecord:
    session: SimulationSession
    config: SimulationConfig
    state: SimulationState = SimulationState.CREATED
    current_time: float = 0.0
    next_input_update: int = 0


class FarcelEngine:
    """Application facade shared by CLI and the future GUI."""

    def __init__(
        self,
        importer: ModelImporter,
        session_factory: SessionFactory | None = None,
        result_exporter: ResultExporter | None = None,
    ) -> None:
        self._importer = importer
        self._session_factory = session_factory
        self._result_exporter = result_exporter
        self._models: dict[str, ModelMetadata] = {}
        self._sessions: dict[str, _SessionRecord] = {}

    def load_fmu(self, path: str | Path) -> ModelMetadata:
        metadata = self._importer.load(Path(path))
        self._models[metadata.model_id] = metadata
        return metadata

    def validate_config(
        self, metadata: ModelMetadata, config: SimulationConfig
    ) -> ValidationReport:
        report = validate_config(metadata, config)
        if not report.is_valid:
            raise EngineError(
                ErrorCode.CONFIG_ERROR,
                "仿真配置验证失败",
                {
                    "issues": tuple(
                        {
                            "field": issue.field,
                            "code": issue.code,
                            "message": issue.message,
                        }
                        for issue in report.issues
                    )
                },
            )
        return report

    def create_session(
        self, model_id: str, config: SimulationConfig
    ) -> SessionHandle:
        metadata = self._models.get(model_id)
        if metadata is None:
            raise EngineError(ErrorCode.INTERNAL_ERROR, "模型尚未加载")
        self.validate_config(metadata, config)
        if self._session_factory is None:
            raise EngineError(ErrorCode.NOT_IMPLEMENTED, "未配置仿真 Session 实现")

        concrete_session = self._session_factory.create(metadata, config)
        handle = SessionHandle(session_id=str(uuid4()))
        self._sessions[handle.session_id] = _SessionRecord(
            session=concrete_session, config=config, current_time=config.start_time
        )
        return handle

    def initialize(self, handle: SessionHandle) -> None:
        record = self._get_session(handle)
        if record.state is not SimulationState.CREATED:
            raise EngineError(ErrorCode.INITIALIZATION_ERROR, "Session 状态不允许初始化")
        record.session.initialize()
        record.state = SimulationState.READY

    def step(
        self, handle: SessionHandle, step_size: float | None = None
    ) -> StepResult:
        record = self._get_session(handle)
        if record.state not in {SimulationState.READY, SimulationState.RUNNING}:
            raise EngineError(ErrorCode.STEP_ERROR, "Session 尚未完成初始化")
        current_time = record.current_time
        actual_step = (
            record.config.communication_step if step_size is None else step_size
        )
        self._apply_scheduled_inputs(record, current_time)
        result = record.session.step(current_time, actual_step)
        if result.status is not StepStatus.SUCCESS:
            raise EngineError(ErrorCode.STEP_ERROR, "FMU step 未成功完成")
        if not math.isfinite(result.reached_time) or result.reached_time <= current_time:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMU step 未返回单调递增的 reached time",
                {
                    "current_time": current_time,
                    "reached_time": result.reached_time,
                },
            )
        record.current_time = result.reached_time
        record.state = SimulationState.RUNNING
        return result

    @staticmethod
    def _apply_scheduled_inputs(record: _SessionRecord, current_time: float) -> None:
        schedule = record.config.input_schedule
        if record.next_input_update >= len(schedule):
            return
        update = schedule[record.next_input_update]
        tolerance = max(1e-12, record.config.communication_step * 1e-9)
        if math.isclose(update.time, current_time, rel_tol=0.0, abs_tol=tolerance):
            if update.values:
                record.session.set_inputs(update.values)
            record.next_input_update += 1

    def read_outputs(self, handle: SessionHandle) -> Mapping[str, Any]:
        record = self._get_session(handle)
        if record.state not in {SimulationState.READY, SimulationState.RUNNING}:
            raise EngineError(
                ErrorCode.OUTPUT_READ_ERROR,
                "Session 尚未完成初始化",
            )
        return record.session.read_outputs()

    def terminate(self, handle: SessionHandle) -> None:
        record = self._get_session(handle)
        if record.state is SimulationState.STOPPED:
            return
        record.state = SimulationState.STOPPING
        try:
            record.session.terminate()
        except BaseException:
            record.state = SimulationState.ERROR
            raise
        record.state = SimulationState.STOPPED

    def get_state(self, handle: SessionHandle) -> SimulationState:
        return self._get_session(handle).state

    def close_session(self, handle: SessionHandle) -> None:
        record = self._sessions.pop(handle.session_id, None)
        if record is None:
            return
        record.session.close()

    def run_fmu(
        self,
        path: str | Path,
        config: SimulationConfig,
        *,
        control: RunControl | None = None,
        on_progress: Callable[[RunProgress], None] | None = None,
        on_result_chunk: Callable[[ResultChunk], None] | None = None,
        result_chunk_size: int = 256,
    ) -> SimulationResult:
        handle: SessionHandle | None = None
        primary_error: EngineError | None = None
        base_error: BaseException | None = None
        simulation_result: SimulationResult | None = None

        _validate_result_chunk_size(result_chunk_size)
        try:
            if control is not None and control.stop_requested:
                raise EngineError(ErrorCode.CANCELLED, "仿真开始前已请求停止")
            metadata = self.load_fmu(path)
            handle = self.create_session(metadata.model_id, config)
            self.initialize(handle)

            current_time = config.start_time
            completed_steps = 0
            result_accumulator = _ResultAccumulator(
                config.selected_outputs, on_result_chunk, result_chunk_size
            )
            tolerance = max(1e-12, config.communication_step * 1e-9)
            output_interval = resolve_output_interval(config)
            _record_sample(
                result_accumulator,
                current_time,
                self.read_outputs(handle) if config.selected_outputs else {},
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

            while current_time < config.stop_time and not math.isclose(
                current_time, config.stop_time, rel_tol=0.0, abs_tol=tolerance
            ):
                if control is not None and control.stop_requested:
                    stopped = True
                    break
                remaining = config.stop_time - current_time
                if remaining < config.communication_step and not supports_variable_step:
                    break
                step_size = min(config.communication_step, remaining)
                result = self.step(handle, step_size)
                current_time = result.reached_time
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
                        self.read_outputs(handle) if config.selected_outputs else {},
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
                    self.read_outputs(handle) if config.selected_outputs else {},
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
            result_accumulator.flush_final()
            self.terminate(handle)
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
        if handle is not None:
            record = self._sessions.get(handle.session_id)
            if record is not None and record.state in {
                SimulationState.READY,
                SimulationState.RUNNING,
            }:
                try:
                    self.terminate(handle)
                except EngineError as exc:
                    cleanup_errors.append(exc)
            try:
                self.close_session(handle)
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
                raise EngineError(
                    primary_error.code, primary_error.message, details
                ) from None
            raise primary_error from None
        if cleanup_error is not None:
            raise cleanup_error from None
        if simulation_result is None:
            raise EngineError(ErrorCode.INTERNAL_ERROR, "仿真未生成结果")
        return simulation_result

    def export_result(
        self, result: SimulationResult, destination: str | Path
    ) -> ExportReport:
        if self._result_exporter is None:
            raise EngineError(ErrorCode.NOT_IMPLEMENTED, "未配置结果导出实现")
        return self._result_exporter.export(result, Path(destination))

    def _get_session(self, handle: SessionHandle) -> _SessionRecord:
        try:
            return self._sessions[handle.session_id]
        except KeyError:
            raise EngineError(ErrorCode.INTERNAL_ERROR, "Session 不存在或已经关闭") from None


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
        missing = tuple(
            name for name in self._selected_outputs if name not in values
        )
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


def _validate_result_chunk_size(result_chunk_size: int) -> None:
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
