from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from fmpy import extract, platform as current_platform, read_model_description
from fmpy.fmi2 import FMU2Model, fmi2Real

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import (
    DiscreteStateUpdate,
    IntegratorStepResult,
    InterfaceType,
    ModelExchangeInitialization,
    ModelMetadata,
    SimulationConfig,
    VariableMetadata,
)
from farcel.infrastructure.fmpy.session import (
    _getter_name,
    _python_scalar,
    _quiet_callbacks,
    _release_native,
    _remove_extraction_directory,
    _setter_name,
)


_MAX_INITIAL_DISCRETE_STATE_ITERATIONS = 1000


class FmpyFmi2ModelExchangeSessionFactory:
    """Create resource-owning FMI 2.0 Model Exchange sessions."""

    def create(
        self, metadata: ModelMetadata, config: SimulationConfig
    ) -> FmpyFmi2ModelExchangeSession:
        if metadata.fmi_version != "2.0":
            raise EngineError(
                ErrorCode.UNSUPPORTED_FMI,
                "本阶段 Model Exchange adapter 只支持 FMI 2.0",
            )
        capability = next(
            (
                item
                for item in metadata.interface_capabilities
                if item.interface_type is InterfaceType.MODEL_EXCHANGE
            ),
            None,
        )
        if capability is None or not capability.model_identifier:
            raise EngineError(
                ErrorCode.UNSUPPORTED_INTERFACE,
                "FMU 不包含可实例化的 FMI 2.0 Model Exchange 接口",
            )
        if capability.needs_execution_tool:
            raise EngineError(
                ErrorCode.UNSUPPORTED_INTERFACE,
                "FMI 2.0 Model Exchange FMU 需要当前不支持的外部执行工具",
            )
        if current_platform not in metadata.platforms:
            raise EngineError(
                ErrorCode.PLATFORM_BINARY_MISSING,
                "FMU 缺少当前平台可执行的 FMI 2.0 Model Exchange 二进制",
                {"platform": current_platform},
            )
        return FmpyFmi2ModelExchangeSession.open(
            metadata=metadata,
            config=config,
            model_identifier=capability.model_identifier,
        )


class FmpyFmi2ModelExchangeSession:
    """Own one FMI2 ``FMU2Model`` and expose Farcel's Model Exchange port."""

    def __init__(
        self,
        metadata: ModelMetadata,
        config: SimulationConfig,
        fmu: Any,
        extraction_directory: Path,
        continuous_state_count: int,
        event_indicator_count: int,
    ) -> None:
        self._metadata = metadata
        self._config = config
        self._fmu = fmu
        self._extraction_directory = extraction_directory
        self._continuous_state_count = continuous_state_count
        self._event_indicator_count = event_indicator_count
        self._initialized = False
        self._continuous_time_mode = False
        self._event_mode = False
        self._terminated = False
        self._closed = False
        self._native_released = False
        self._initialization: ModelExchangeInitialization | None = None
        self._fmu_diagnostics: list[str] = []

    @classmethod
    def open(
        cls,
        metadata: ModelMetadata,
        config: SimulationConfig,
        model_identifier: str,
    ) -> FmpyFmi2ModelExchangeSession:
        extraction_directory = Path(tempfile.mkdtemp(prefix="farcel-fmi2-me-"))
        fmu: Any = None
        instantiated = False
        cleanup_diagnostics: list[str] = []

        try:
            description = read_model_description(metadata.source_path)
            extract(metadata.source_path, unzipdir=extraction_directory)
            fmu = FMU2Model.__new__(FMU2Model)
            working_directory = os.getcwd()
            try:
                FMU2Model.__init__(
                    fmu,
                    guid=metadata.instantiation_token,
                    unzipDirectory=str(extraction_directory),
                    modelIdentifier=model_identifier,
                    instanceName=f"farcel_me_{uuid4().hex}",
                )
            finally:
                os.chdir(working_directory)
            diagnostics: list[str] = []
            fmu.instantiate(callbacks=_quiet_callbacks(diagnostics), loggingOn=True)
            instantiated = True
            session = cls(
                metadata,
                config,
                fmu,
                extraction_directory,
                int(description.numberOfContinuousStates),
                int(description.numberOfEventIndicators),
            )
            session._fmu_diagnostics = diagnostics
            return session
        except Exception as exc:
            cleanup_diagnostics.extend(_release_native(fmu, instantiated))
            cleanup_diagnostics.extend(_remove_extraction_directory(extraction_directory))
            details: dict[str, Any] = {"diagnostic": str(exc)}
            if cleanup_diagnostics:
                details["cleanup_diagnostics"] = tuple(cleanup_diagnostics)
            raise EngineError(
                ErrorCode.INSTANTIATION_ERROR,
                "FMI 2.0 Model Exchange instance 创建失败",
                details,
            ) from None

    def initialize(self) -> ModelExchangeInitialization:
        if self._closed:
            raise EngineError(ErrorCode.INITIALIZATION_ERROR, "Session 已关闭")
        if self._initialization is not None:
            return self._initialization

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
        if self._config.initial_inputs:
            self.set_inputs(self._config.initial_inputs)
        try:
            self._fmu.enterInitializationMode()
            self._fmu.exitInitializationMode()
        except Exception as exc:
            raise EngineError(
                ErrorCode.INITIALIZATION_ERROR,
                "FMI initialization mode 失败",
                {"diagnostic": str(exc)},
            ) from None

        last_update = DiscreteStateUpdate(discrete_states_need_update=False)
        for iteration_count in range(1, _MAX_INITIAL_DISCRETE_STATE_ITERATIONS + 1):
            last_update = self._new_discrete_states()
            if last_update.terminate_requested or not last_update.discrete_states_need_update:
                break
        else:
            raise EngineError(
                ErrorCode.INITIALIZATION_ERROR,
                "FMI 初始离散状态迭代超过上限",
                {
                    "phase": "initial_discrete_state_iteration",
                    "iteration_count": _MAX_INITIAL_DISCRETE_STATE_ITERATIONS,
                },
            )

        initialization = ModelExchangeInitialization(
            continuous_state_count=self._continuous_state_count,
            event_indicator_count=self._event_indicator_count,
            terminate_requested=last_update.terminate_requested,
            continuous_states_changed=last_update.continuous_states_changed,
            nominals_changed=last_update.nominals_changed,
            next_event_time_defined=last_update.next_event_time_defined,
            next_event_time=last_update.next_event_time,
        )
        self._initialized = True
        self._event_mode = not last_update.terminate_requested
        self._initialization = initialization
        if not last_update.terminate_requested:
            self.enter_continuous_time_mode()
        return initialization

    def set_inputs(self, values: Mapping[str, Any]) -> None:
        if self._terminated or self._closed:
            raise EngineError(ErrorCode.INPUT_SET_ERROR, "Session 状态不允许设置 input")
        variables = {variable.name: variable for variable in self._metadata.variables}
        try:
            for name, value in values.items():
                variable = variables[name]
                setter = getattr(self._fmu, _setter_name(variable))
                setter([variable.value_reference], [value])
        except EngineError:
            raise
        except Exception as exc:
            raise EngineError(
                ErrorCode.INPUT_SET_ERROR,
                "FMU input 设置失败",
                {"input": name, "diagnostic": str(exc)},
            ) from None

    def set_time(self, time: float) -> None:
        self._require_continuous_time_mode()
        if (
            isinstance(time, bool)
            or not isinstance(time, (int, float))
            or not math.isfinite(time)
        ):
            raise EngineError(ErrorCode.STEP_ERROR, "time 必须是有限数值")
        try:
            self._fmu.setTime(float(time))
        except Exception as exc:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMI setTime 失败",
                {"diagnostic": str(exc)},
            ) from None

    def get_continuous_states(self) -> tuple[float, ...]:
        self._require_continuous_time_mode()
        if self._continuous_state_count == 0:
            return ()
        values = (fmi2Real * self._continuous_state_count)()
        try:
            self._fmu.getContinuousStates(values, self._continuous_state_count)
        except Exception as exc:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMI getContinuousStates 失败",
                {"diagnostic": str(exc)},
            ) from None
        return tuple(float(value) for value in values)

    def set_continuous_states(self, states: tuple[float, ...]) -> None:
        self._require_continuous_time_mode()
        if len(states) != self._continuous_state_count:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "continuous states 长度与 FMU 状态维度不一致",
                {
                    "expected_count": self._continuous_state_count,
                    "actual_count": len(states),
                },
            )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in states
        ):
            raise EngineError(ErrorCode.STEP_ERROR, "continuous states 必须是有限数值")
        if self._continuous_state_count == 0:
            return
        values = (fmi2Real * self._continuous_state_count)(*states)
        try:
            self._fmu.setContinuousStates(values, self._continuous_state_count)
        except Exception as exc:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMI setContinuousStates 失败",
                {"diagnostic": str(exc)},
            ) from None

    def get_derivatives(self) -> tuple[float, ...]:
        self._require_continuous_time_mode()
        if self._continuous_state_count == 0:
            return ()
        values = (fmi2Real * self._continuous_state_count)()
        try:
            self._fmu.getDerivatives(values, self._continuous_state_count)
        except Exception as exc:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMI getDerivatives 失败",
                {"diagnostic": str(exc)},
            ) from None
        return tuple(float(value) for value in values)

    def get_event_indicators(self) -> tuple[float, ...]:
        self._require_continuous_time_mode()
        if self._event_indicator_count == 0:
            return ()
        values = (fmi2Real * self._event_indicator_count)()
        try:
            self._fmu.getEventIndicators(values, self._event_indicator_count)
        except Exception as exc:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMI getEventIndicators 失败",
                {"diagnostic": str(exc)},
            ) from None
        return tuple(float(value) for value in values)

    def completed_integrator_step(self) -> IntegratorStepResult:
        self._require_continuous_time_mode()
        try:
            enter_event_mode, terminate_requested = self._fmu.completedIntegratorStep()
        except Exception as exc:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMI completedIntegratorStep 失败",
                {"diagnostic": str(exc)},
            ) from None
        return IntegratorStepResult(
            enter_event_mode=bool(enter_event_mode),
            terminate_requested=bool(terminate_requested),
        )

    def enter_event_mode(self) -> None:
        self._require_initialized()
        try:
            self._fmu.enterEventMode()
        except Exception as exc:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMI enterEventMode 失败",
                {"diagnostic": str(exc)},
            ) from None
        self._continuous_time_mode = False
        self._event_mode = True

    def update_discrete_states(self) -> DiscreteStateUpdate:
        self._require_initialized()
        if not self._event_mode:
            raise EngineError(ErrorCode.STEP_ERROR, "Session 当前不在 Event Mode")
        return self._new_discrete_states()

    def enter_continuous_time_mode(self) -> None:
        self._require_initialized()
        try:
            self._fmu.enterContinuousTimeMode()
        except Exception as exc:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMI enterContinuousTimeMode 失败",
                {"diagnostic": str(exc)},
            ) from None
        self._event_mode = False
        self._continuous_time_mode = True

    def read_outputs(self) -> dict[str, Any]:
        self._require_initialized(error_code=ErrorCode.OUTPUT_READ_ERROR)
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
                getter = getattr(self._fmu, _getter_name(variable))
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
        self._continuous_time_mode = False
        self._event_mode = False

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

    def _new_discrete_states(self) -> DiscreteStateUpdate:
        try:
            (
                discrete_states_need_update,
                terminate_requested,
                nominals_changed,
                continuous_states_changed,
                next_event_time_defined,
                next_event_time,
            ) = self._fmu.newDiscreteStates()
        except Exception as exc:
            raise EngineError(
                ErrorCode.INITIALIZATION_ERROR if not self._initialized else ErrorCode.STEP_ERROR,
                "FMI newDiscreteStates 失败",
                {"diagnostic": str(exc)},
            ) from None
        return DiscreteStateUpdate(
            discrete_states_need_update=bool(discrete_states_need_update),
            terminate_requested=bool(terminate_requested),
            continuous_states_changed=bool(continuous_states_changed),
            nominals_changed=bool(nominals_changed),
            next_event_time_defined=bool(next_event_time_defined),
            next_event_time=float(next_event_time) if next_event_time_defined else None,
        )

    def _apply_parameters(self) -> None:
        variables = {variable.name: variable for variable in self._metadata.variables}
        try:
            for name, value in self._config.parameters.items():
                variable = variables[name]
                setter = getattr(self._fmu, _setter_name(variable))
                setter([variable.value_reference], [value])
        except Exception as exc:
            raise EngineError(
                ErrorCode.PARAMETER_SET_ERROR,
                "FMU parameter override 应用失败",
                {"parameter": name, "diagnostic": str(exc)},
            ) from None

    def _require_initialized(self, *, error_code: ErrorCode = ErrorCode.STEP_ERROR) -> None:
        if not self._initialized or self._terminated or self._closed:
            raise EngineError(error_code, "Session 状态不允许执行 Model Exchange 操作")

    def _require_continuous_time_mode(self) -> None:
        self._require_initialized()
        if not self._continuous_time_mode:
            raise EngineError(ErrorCode.STEP_ERROR, "Session 当前不在 Continuous-Time Mode")
