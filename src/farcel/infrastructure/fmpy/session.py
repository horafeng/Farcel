from __future__ import annotations

import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from fmpy import calloc, extract, free
from fmpy.fmi2 import (
    FMU2Slave,
    fmi2CallbackAllocateMemoryTYPE,
    fmi2CallbackFreeMemoryTYPE,
    fmi2CallbackFunctions,
    fmi2CallbackLoggerTYPE,
)

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import (
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    StepResult,
    StepStatus,
    VariableMetadata,
)


class FmpyFmi2SessionFactory:
    """Create resource-owning FMI 2.0 Co-Simulation sessions."""

    def create(
        self, metadata: ModelMetadata, config: SimulationConfig
    ) -> FmpyFmi2Session:
        if metadata.fmi_version != "2.0":
            raise EngineError(
                ErrorCode.UNSUPPORTED_FMI,
                "本阶段 runtime 只支持 FMI 2.0 Co-Simulation",
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
                "FMU 不包含可实例化的 FMI 2.0 Co-Simulation 接口",
            )
        return FmpyFmi2Session.open(
            metadata=metadata,
            config=config,
            model_identifier=capability.model_identifier,
        )


class FmpyFmi2Session:
    """Own one FMU2Slave, its native library, and its extraction directory."""

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
    ) -> FmpyFmi2Session:
        extraction_directory = Path(tempfile.mkdtemp(prefix="farcel-fmi2-"))
        fmu: Any = None
        instantiated = False
        cleanup_diagnostics: list[str] = []

        try:
            extract(metadata.source_path, unzipdir=extraction_directory)
            fmu = FMU2Slave.__new__(FMU2Slave)
            working_directory = os.getcwd()
            try:
                FMU2Slave.__init__(
                    fmu,
                    guid=metadata.instantiation_token,
                    unzipDirectory=str(extraction_directory),
                    modelIdentifier=model_identifier,
                    instanceName=f"farcel_{uuid4().hex}",
                )
            finally:
                os.chdir(working_directory)
            fmu.instantiate(callbacks=_quiet_callbacks())
            instantiated = True
            return cls(metadata, config, fmu, extraction_directory)
        except Exception as exc:
            cleanup_diagnostics.extend(_release_native(fmu, instantiated))
            cleanup_diagnostics.extend(_remove_extraction_directory(extraction_directory))
            details: dict[str, Any] = {"diagnostic": str(exc)}
            if cleanup_diagnostics:
                details["cleanup_diagnostics"] = tuple(cleanup_diagnostics)
            raise EngineError(
                ErrorCode.INSTANTIATION_ERROR,
                "FMI 2.0 Co-Simulation instance 创建失败",
                details,
            ) from None

    def initialize(self) -> None:
        if self._closed:
            raise EngineError(ErrorCode.INITIALIZATION_ERROR, "Session 已关闭")
        if self._initialized:
            return

        try:
            self._fmu.setupExperiment(
                tolerance=self._config.relative_tolerance,
                startTime=self._config.start_time,
                stopTime=self._config.stop_time,
            )
        except Exception as exc:
            raise EngineError(
                ErrorCode.INITIALIZATION_ERROR,
                "FMI setupExperiment 失败",
                {"diagnostic": str(exc)},
            ) from None

        self._apply_parameters()

        try:
            self._fmu.enterInitializationMode()
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
            self._fmu.doStep(
                currentCommunicationPoint=current_time,
                communicationStepSize=step_size,
            )
        except Exception as exc:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMI doStep 失败",
                {"diagnostic": str(exc)},
            ) from None

        return StepResult(
            requested_time=requested_time,
            reached_time=requested_time,
            step_size=step_size,
            status=StepStatus.SUCCESS,
        )

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
                setter_name = _setter_name(variable)
                setter = getattr(self._fmu, setter_name)
                setter([variable.value_reference], [value])
                applied.append(name)
        except Exception as exc:
            raise EngineError(
                ErrorCode.PARAMETER_SET_ERROR,
                "FMU parameter override 应用失败",
                {"parameter": name, "diagnostic": str(exc)},
            ) from None
        self._applied_parameters = tuple(applied)


def _setter_name(variable: VariableMetadata) -> str:
    if variable.data_type == "Enumeration":
        return "setInteger"
    return f"set{variable.data_type}"


def _quiet_callbacks() -> fmi2CallbackFunctions:
    callbacks = fmi2CallbackFunctions()
    callbacks.logger = fmi2CallbackLoggerTYPE(lambda *_: None)
    callbacks.allocateMemory = fmi2CallbackAllocateMemoryTYPE(calloc)
    callbacks.freeMemory = fmi2CallbackFreeMemoryTYPE(free)
    return callbacks


def _release_native(fmu: Any, instantiated: bool) -> list[str]:
    if fmu is None:
        return []

    diagnostics: list[str] = []
    if instantiated:
        try:
            fmu.freeInstance()
            return diagnostics
        except Exception as exc:
            diagnostics.append(f"freeInstance: {exc}")

    if hasattr(fmu, "dll"):
        try:
            fmu.freeLibrary()
        except Exception as exc:
            diagnostics.append(f"freeLibrary: {exc}")
    return diagnostics


def _remove_extraction_directory(directory: Path) -> list[str]:
    if not directory.exists():
        return []
    try:
        shutil.rmtree(directory)
        return []
    except Exception as exc:
        return [f"remove temporary directory: {exc}"]
