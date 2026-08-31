from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from fmpy import extract
from fmpy.fmi3 import FMU3Slave

from farcel.contracts._arrays import array_size, flatten_array, reshape_array
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


_MAX_EVENT_ITERATIONS = 1000


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


def _co_simulation_capability(metadata: ModelMetadata):
    return next(
        (
            item
            for item in metadata.interface_capabilities
            if item.interface_type is InterfaceType.CO_SIMULATION
        ),
        None,
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
        capability = _co_simulation_capability(metadata)
        self._event_mode_used = bool(capability and capability.supports_event_mode)
        self._early_return_allowed = bool(
            capability and capability.supports_early_return
        )

    @classmethod
    def open(
        cls,
        metadata: ModelMetadata,
        config: SimulationConfig,
        model_identifier: str,
    ) -> FmpyFmi3Session:
        extraction_directory = Path(tempfile.mkdtemp(prefix="farcel-fmi3-"))
        capability = _co_simulation_capability(metadata)
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
                eventModeUsed=bool(capability and capability.supports_event_mode),
                earlyReturnAllowed=bool(
                    capability and capability.supports_early_return
                ),
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

        try:
            self._fmu.enterInitializationMode(
                tolerance=self._config.relative_tolerance,
                startTime=self._config.start_time,
                stopTime=self._config.stop_time,
            )
        except Exception as exc:
            raise EngineError(
                ErrorCode.INITIALIZATION_ERROR,
                "FMI initialization mode 失败",
                {"diagnostic": str(exc)},
            ) from None

        # Fixed parameters and initial inputs are set while the FMU is in
        # Initialization Mode.  This is required by StateSpace and avoids
        # entering FMI 3 Configuration Mode, which is deliberately out of
        # scope for resolved/default-dimension arrays.
        self._apply_parameters()
        if self._config.initial_inputs:
            self.set_inputs(self._config.initial_inputs)

        try:
            self._fmu.exitInitializationMode()
        except Exception as exc:
            raise EngineError(
                ErrorCode.INITIALIZATION_ERROR,
                "FMI initialization mode 失败",
                {"diagnostic": str(exc)},
            ) from None

        if self._event_mode_used:
            self._complete_event_mode(phase="initialization")

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

        if terminate_requested:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMI 3 Co-Simulation 请求终止仿真",
                {
                    "terminate_requested": True,
                    "reached_time": reached_time,
                },
            )
        if event_encountered and not self._event_mode_used:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMI 3 runtime 返回当前配置未启用的 Event Mode",
                {"conditions": ("event_mode",), "reached_time": reached_time},
            )
        if early_return and not self._early_return_allowed:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMI 3 runtime 返回当前配置未允许的 Early Return",
                {"conditions": ("early_return",), "reached_time": reached_time},
            )
        if not math.isfinite(reached_time) or reached_time <= current_time:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMI 3 doStep 未返回有效的 reached time",
                {
                    "current_time": current_time,
                    "requested_time": requested_time,
                    "reached_time": reached_time,
                    "early_return": early_return,
                },
            )
        if event_encountered:
            self._enter_event_mode()
            self._complete_event_mode(phase="runtime")

        return StepResult(
            requested_time=requested_time,
            reached_time=reached_time,
            step_size=step_size,
            status=StepStatus.SUCCESS,
            event_encountered=event_encountered,
            early_return=early_return,
            terminate_requested=terminate_requested,
        )

    def _enter_event_mode(self) -> None:
        try:
            self._fmu.enterEventMode()
        except Exception as exc:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMI 3 Event Mode 进入失败",
                {"diagnostic": str(exc)},
            ) from None

    def _complete_event_mode(self, *, phase: str) -> None:
        for event_iteration_count in range(1, _MAX_EVENT_ITERATIONS + 1):
            try:
                (
                    discrete_states_need_update,
                    terminate_requested,
                    _,
                    _,
                    _,
                    _,
                ) = self._fmu.updateDiscreteStates()
            except Exception as exc:
                raise EngineError(
                    ErrorCode.STEP_ERROR,
                    "FMI 3 Event Mode 离散状态更新失败",
                    {"phase": phase, "diagnostic": str(exc)},
                ) from None

            if terminate_requested:
                raise EngineError(
                    ErrorCode.STEP_ERROR,
                    "FMI 3 Event Mode 请求终止仿真",
                    {
                        "phase": phase,
                        "terminate_requested": True,
                        "event_iteration_count": event_iteration_count,
                    },
                )
            if not discrete_states_need_update:
                break
        else:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMI 3 Event Mode 离散状态更新超过迭代上限",
                {
                    "phase": phase,
                    "event_iteration_count": _MAX_EVENT_ITERATIONS,
                },
            )
        try:
            self._fmu.enterStepMode()
        except Exception as exc:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMI 3 Step Mode 进入失败",
                {"phase": phase, "diagnostic": str(exc)},
            ) from None

    def set_inputs(self, values: Mapping[str, Any]) -> None:
        if self._terminated or self._closed:
            raise EngineError(ErrorCode.INPUT_SET_ERROR, "Session 状态不允许设置 input")
        variables = {variable.name: variable for variable in self._metadata.variables}
        try:
            for name, value in values.items():
                variable = variables[name]
                self._set_variable(variable, value)
        except EngineError:
            raise
        except Exception as exc:
            raise EngineError(
                ErrorCode.INPUT_SET_ERROR,
                "FMU input 设置失败",
                {"input": name, "diagnostic": str(exc)},
            ) from None

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
                if variable.shape:
                    raw_values = getter(
                        [variable.value_reference], nValues=array_size(variable.shape)
                    )
                    values[name] = reshape_array(
                        tuple(_python_scalar(value, variable) for value in raw_values),
                        variable.shape,
                    )
                else:
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
                self._set_variable(variable, value)
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

    def _set_variable(self, variable: VariableMetadata, value: Any) -> None:
        setter = getattr(self._fmu, _accessor_name("set", variable))
        values = (
            flatten_array(value, variable.shape) if variable.shape else (value,)
        )
        setter([variable.value_reference], list(values))


def _accessor_name(prefix: str, variable: VariableMetadata) -> str:
    if variable.data_type == "Enumeration":
        return f"{prefix}Int64"
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
        "Enumeration",
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
