from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from fmpy import extract
from fmpy.fmi3 import FMU3Slave

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import (
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    StepResult,
    StepStatus,
    VariableMetadata,
)
from farcel.infrastructure.fmpy.session import (
    _release_native,
    _remove_extraction_directory,
)


class FmpyFmi3SessionFactory:
    """Create resource-owning basic FMI 3.0 Co-Simulation sessions."""

    def create(
        self, metadata: ModelMetadata, config: SimulationConfig
    ) -> FmpyFmi3Session:
        if metadata.fmi_version != "3.0":
            raise EngineError(
                ErrorCode.UNSUPPORTED_FMI,
                "该 Session 实现只支持 FMI 3.0 Co-Simulation",
            )
        capability = next(
            (
                item
                for item in metadata.interface_capabilities
                if item.interface_type is InterfaceType.CO_SIMULATION
            ),
            None,
        )
        if capability is None or not capability.model_identifier:
            raise EngineError(
                ErrorCode.UNSUPPORTED_INTERFACE,
                "FMU 不包含可实例化的 FMI 3.0 Co-Simulation 接口",
            )
        return FmpyFmi3Session.open(
            metadata=metadata,
            config=config,
            model_identifier=capability.model_identifier,
        )


class FmpyFmi3Session:
    """Own one basic FMU3Slave and all of its native resources."""

    def __init__(
        self,
        metadata: ModelMetadata,
        config: SimulationConfig,
        fmu: Any,
        extraction_directory: Path,
    ) -> None:
        self._metadata = metadata
        self._config = config
        self._fmu = fmu
        self._extraction_directory = extraction_directory
        self._initialized = False
        self._terminated = False
        self._closed = False
        self._native_released = False
        self._applied_parameters: tuple[str, ...] = ()

    @classmethod
    def open(
        cls,
        metadata: ModelMetadata,
        config: SimulationConfig,
        model_identifier: str,
    ) -> FmpyFmi3Session:
        extraction_directory = Path(tempfile.mkdtemp(prefix="farcel-fmi3-"))
        fmu: Any = None
        instantiated = False
        cleanup_diagnostics: list[str] = []

        try:
            extract(metadata.source_path, unzipdir=extraction_directory)
            fmu = FMU3Slave.__new__(FMU3Slave)
            working_directory = os.getcwd()
            try:
                FMU3Slave.__init__(
                    fmu,
                    guid=metadata.instantiation_token,
                    unzipDirectory=str(extraction_directory),
                    modelIdentifier=model_identifier,
                    instanceName=f"farcel_{uuid4().hex}",
                )
            finally:
                os.chdir(working_directory)
            fmu.instantiate(
                eventModeUsed=False,
                earlyReturnAllowed=False,
                logMessage=lambda *_: None,
            )
            instantiated = True
            return cls(metadata, config, fmu, extraction_directory)
        except Exception as exc:
            cleanup_diagnostics.extend(_release_native(fmu, instantiated))
            cleanup_diagnostics.extend(
                _remove_extraction_directory(extraction_directory)
            )
            details: dict[str, Any] = {"diagnostic": str(exc)}
            if cleanup_diagnostics:
                details["cleanup_diagnostics"] = tuple(cleanup_diagnostics)
            raise EngineError(
                ErrorCode.INSTANTIATION_ERROR,
                "FMI 3.0 Co-Simulation instance 创建失败",
                details,
            ) from None

    def initialize(self) -> None:
        if self._closed:
            raise EngineError(ErrorCode.INITIALIZATION_ERROR, "Session 已关闭")
        if self._initialized:
            return

        self._apply_parameters()

        try:
            self._fmu.enterInitializationMode(
                tolerance=self._config.relative_tolerance,
                startTime=self._config.start_time,
                stopTime=self._config.stop_time,
            )
            self._fmu.exitInitializationMode()
        except Exception as exc:
            raise EngineError(
                ErrorCode.INITIALIZATION_ERROR,
                "FMI initialization mode 失败",
                {"diagnostic": str(exc)},
            ) from None

        self._initialized = True

    def step(self, current_time: float, step_size: float) -> StepResult:
        if not self._initialized or self._terminated or self._closed:
            raise EngineError(ErrorCode.STEP_ERROR, "Session 状态不允许执行 step")
        if not math.isfinite(step_size) or step_size <= 0:
            raise EngineError(ErrorCode.STEP_ERROR, "step size 必须大于 0")

        requested_time = current_time + step_size
        try:
            (
                event_encountered,
                terminate_requested,
                early_return,
                reached_time,
            ) = self._fmu.doStep(
                currentCommunicationPoint=current_time,
                communicationStepSize=step_size,
            )
        except Exception as exc:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMI doStep 失败",
                {"diagnostic": str(exc)},
            ) from None

        if event_encountered or early_return or terminate_requested:
            conditions = tuple(
                name
                for name, active in (
                    ("event_mode", event_encountered),
                    ("early_return", early_return),
                    ("terminate_requested", terminate_requested),
                )
                if active
            )
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMI 3 runtime 返回当前 Basic Co-Simulation 不支持的条件",
                {"conditions": conditions, "reached_time": reached_time},
            )
        if not math.isfinite(reached_time) or reached_time <= current_time:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMI 3 doStep 未返回有效的 reached time",
                {"current_time": current_time, "reached_time": reached_time},
            )

        return StepResult(
            requested_time=requested_time,
            reached_time=reached_time,
            step_size=step_size,
            status=StepStatus.SUCCESS,
        )

    def read_outputs(self) -> dict[str, Any]:
        if not self._initialized or self._terminated or self._closed:
            raise EngineError(
                ErrorCode.OUTPUT_READ_ERROR,
                "Session 状态不允许读取输出变量",
            )

        variables = {variable.name: variable for variable in self._metadata.variables}
        values: dict[str, Any] = {}
        for name in self._config.selected_outputs:
            variable = variables.get(name)
            if variable is None:
                raise EngineError(
                    ErrorCode.OUTPUT_READ_ERROR,
                    "所选输出变量不在已加载模型中",
                    {"variable": name},
                )
            try:
                getter = getattr(self._fmu, _accessor_name("get", variable))
                raw_value = getter([variable.value_reference])[0]
                values[name] = _python_scalar(raw_value, variable)
            except EngineError:
                raise
            except Exception as exc:
                raise EngineError(
                    ErrorCode.OUTPUT_READ_ERROR,
                    "FMU 输出变量读取失败",
                    {"variable": name, "diagnostic": str(exc)},
                ) from None
        return values

    def terminate(self) -> None:
        if self._terminated:
            return
        if self._closed:
            raise EngineError(ErrorCode.TERMINATION_ERROR, "Session 已关闭")
        if not self._initialized:
            return
        try:
            self._fmu.terminate()
        except Exception as exc:
            raise EngineError(
                ErrorCode.TERMINATION_ERROR,
                "FMI terminate 失败",
                {"diagnostic": str(exc)},
            ) from None
        self._terminated = True

    def close(self) -> None:
        if self._closed:
            return

        diagnostics: list[str] = []
        if not self._native_released:
            diagnostics.extend(_release_native(self._fmu, instantiated=True))
            self._native_released = True
            self._fmu = None
        diagnostics.extend(_remove_extraction_directory(self._extraction_directory))
        self._closed = self._native_released and not self._extraction_directory.exists()

        if diagnostics:
            raise EngineError(
                ErrorCode.CLEANUP_ERROR,
                "FMU native resources 或临时目录释放失败",
                {"diagnostics": tuple(diagnostics)},
            )

    def _apply_parameters(self) -> None:
        variables = {variable.name: variable for variable in self._metadata.variables}
        applied: list[str] = []
        try:
            for name, value in self._config.parameters.items():
                variable = variables[name]
                setter = getattr(self._fmu, _accessor_name("set", variable))
                setter([variable.value_reference], [value])
                applied.append(name)
        except EngineError:
            raise
        except Exception as exc:
            raise EngineError(
                ErrorCode.PARAMETER_SET_ERROR,
                "FMU parameter override 应用失败",
                {"parameter": name, "diagnostic": str(exc)},
            ) from None
        self._applied_parameters = tuple(applied)


def _accessor_name(prefix: str, variable: VariableMetadata) -> str:
    if variable.shape:
        code = (
            ErrorCode.PARAMETER_SET_ERROR
            if prefix == "set"
            else ErrorCode.OUTPUT_READ_ERROR
        )
        raise EngineError(
            code,
            "本阶段不支持 FMI 3 数组变量",
            {"variable": variable.name},
        )
    supported_types = {
        "Float32",
        "Float64",
        "Int8",
        "UInt8",
        "Int16",
        "UInt16",
        "Int32",
        "UInt32",
        "Int64",
        "UInt64",
        "Boolean",
        "String",
    }
    if variable.data_type not in supported_types:
        code = (
            ErrorCode.PARAMETER_SET_ERROR
            if prefix == "set"
            else ErrorCode.OUTPUT_READ_ERROR
        )
        raise EngineError(
            code,
            "本阶段不支持该 FMI 3 标量变量类型",
            {"variable": variable.name, "data_type": variable.data_type},
        )
    return f"{prefix}{variable.data_type}"


def _python_scalar(value: Any, variable: VariableMetadata) -> Any:
    if variable.data_type in {"Float32", "Float64"}:
        return float(value)
    if variable.data_type in {
        "Int8",
        "UInt8",
        "Int16",
        "UInt16",
        "Int32",
        "UInt32",
        "Int64",
        "UInt64",
    }:
        return int(value)
    if variable.data_type == "Boolean":
        return bool(value)
    if variable.data_type == "String":
        return str(value)
    raise EngineError(
        ErrorCode.OUTPUT_READ_ERROR,
        "本阶段不支持该 FMI 3 标量变量类型",
        {"variable": variable.name, "data_type": variable.data_type},
    )
