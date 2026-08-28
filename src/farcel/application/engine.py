from __future__ import annotations

import math
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
    SessionHandle,
    SimulationConfig,
    SimulationResult,
    SimulationState,
    StepResult,
    StepStatus,
    ValidationReport,
)
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

    def run_fmu(self, path: str | Path, config: SimulationConfig) -> SimulationResult:
        handle: SessionHandle | None = None
        primary_error: EngineError | None = None
        base_error: BaseException | None = None
        simulation_result: SimulationResult | None = None

        try:
            metadata = self.load_fmu(path)
            handle = self.create_session(metadata.model_id, config)
            self.initialize(handle)

            current_time = config.start_time
            completed_steps = 0
            timestamps = [current_time]
            output_columns = {name: [] for name in config.selected_outputs}
            tolerance = max(1e-12, config.communication_step * 1e-9)
            output_interval = resolve_output_interval(config)
            if config.selected_outputs:
                _append_output_sample(
                    output_columns,
                    config.selected_outputs,
                    self.read_outputs(handle),
                )
            supports_variable_step = _supports_variable_step(metadata)

            while current_time < config.stop_time and not math.isclose(
                current_time, config.stop_time, rel_tol=0.0, abs_tol=tolerance
            ):
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
                    timestamps.append(current_time)
                    if config.selected_outputs:
                        _append_output_sample(
                            output_columns,
                            config.selected_outputs,
                            self.read_outputs(handle),
                        )

            if not math.isclose(
                timestamps[-1], current_time, rel_tol=0.0, abs_tol=tolerance
            ):
                timestamps.append(current_time)
                if config.selected_outputs:
                    _append_output_sample(
                        output_columns,
                        config.selected_outputs,
                        self.read_outputs(handle),
                    )

            self.terminate(handle)
            simulation_result = SimulationResult(
                fmu_path=str(Path(path).expanduser().resolve()),
                start_time=config.start_time,
                stop_time=config.stop_time,
                step_size=config.communication_step,
                completed_steps=completed_steps,
                final_time=current_time,
                completion_state=SimulationState.COMPLETED,
                timestamps=tuple(timestamps),
                outputs={
                    name: tuple(values) for name, values in output_columns.items()
                },
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


def _append_output_sample(
    columns: dict[str, list[Any]],
    selected_outputs: tuple[str, ...],
    sample: Mapping[str, Any],
) -> None:
    missing = tuple(name for name in selected_outputs if name not in sample)
    if missing:
        raise EngineError(
            ErrorCode.OUTPUT_READ_ERROR,
            "Session 未返回全部所选输出变量",
            {"variables": missing},
        )
    for name in selected_outputs:
        columns[name].append(sample[name])


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
