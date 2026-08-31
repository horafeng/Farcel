from __future__ import annotations

from pathlib import Path
from typing import Any

from fmpy import platform as current_platform
from fmpy import read_model_description, supported_platforms
from fmpy.model_description import ValidationError

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import (
    CapabilitySet,
    DefaultExperiment,
    InterfaceCapability,
    InterfaceType,
    ModelMetadata,
    VariableMetadata,
)
from farcel.contracts._arrays import array_size, reshape_array


class FmpyImporter:
    """Read an FMU with FMPy and return only Farcel-owned data types."""

    def load(self, path: Path) -> ModelMetadata:
        path = path.expanduser().resolve()
        if path.suffix.lower() != ".fmu":
            raise EngineError(ErrorCode.IMPORT_ERROR, "输入文件必须使用 .fmu 扩展名")
        if not path.is_file():
            raise EngineError(ErrorCode.IMPORT_ERROR, "FMU 文件不存在")

        error: EngineError | None = None
        validation_diagnostics: tuple[str, ...] = ()
        try:
            description = read_model_description(path, validate=True)
            platforms = tuple(supported_platforms(path))
        except ValidationError as exc:
            validation_diagnostics = tuple(exc.problems)
            if _only_recoverable_validation_problems(validation_diagnostics):
                try:
                    description = read_model_description(path, validate=False)
                    platforms = tuple(supported_platforms(path))
                except Exception as fallback_exc:
                    error = EngineError(
                        ErrorCode.IMPORT_ERROR,
                        "无法读取 FMU 元数据",
                        {"diagnostic": str(fallback_exc)},
                    )
            else:
                error = EngineError(
                    ErrorCode.VALIDATION_ERROR,
                    "modelDescription.xml 未通过 FMI 校验",
                    {"diagnostics": validation_diagnostics},
                )
        except Exception as exc:
            error = EngineError(
                ErrorCode.IMPORT_ERROR,
                "无法读取 FMU 元数据",
                {"diagnostic": str(exc)},
            )

        # Raise after leaving the except block so FMPy's validation traceback does
        # not retain an open ZipExtFile on Windows.
        if error is not None:
            raise error from None

        if description.fmiVersion not in {"2.0", "3.0"}:
            raise EngineError(
                ErrorCode.UNSUPPORTED_FMI,
                "Farcel 当前只支持 FMI 2.0 和 FMI 3.0 元数据",
                {"fmi_version": description.fmiVersion, "parseable": True},
            )

        interface_capabilities = _map_interfaces(description, platforms)
        executable = next(
            (
                item.interface_type
                for item in interface_capabilities
                if item.can_execute
            ),
            None,
        )
        co_simulation = next(
            (
                item
                for item in interface_capabilities
                if item.interface_type is InterfaceType.CO_SIMULATION
            ),
            None,
        )

        diagnostics = [
            f"FMI validation warning: {problem}"
            for problem in validation_diagnostics
        ]
        if executable is None:
            if co_simulation is None:
                diagnostics.append("FMU 可解析，但当前不包含 Co-Simulation 接口")
            elif co_simulation.needs_execution_tool:
                diagnostics.append("FMU 可解析，但需要 Farcel 未提供的外部执行工具")
            else:
                diagnostics.append(
                    f"FMU 可解析，但缺少当前平台二进制: {current_platform}"
                )

        token = description.instantiationToken
        return ModelMetadata(
            model_id=token or description.modelName,
            source_path=str(path),
            fmi_version=description.fmiVersion,
            model_name=description.modelName,
            interface_types=tuple(
                capability.interface_type for capability in interface_capabilities
            ),
            instantiation_token=token,
            executable_interface=executable,
            description=description.description,
            generation_tool=description.generationTool,
            generation_time=description.generationDateAndTime,
            platforms=platforms,
            interface_capabilities=interface_capabilities,
            default_experiment=_map_default_experiment(description.defaultExperiment),
            capabilities=CapabilitySet(
                can_execute=executable is not None,
                supports_event_mode=bool(
                    co_simulation and co_simulation.supports_event_mode
                ),
                supports_early_return=bool(
                    co_simulation and co_simulation.supports_early_return
                ),
            ),
            variables=tuple(_map_variable(variable) for variable in description.modelVariables),
            diagnostics=tuple(diagnostics),
        )


def _map_interfaces(description: Any, platforms: tuple[str, ...]) -> tuple[InterfaceCapability, ...]:
    result: list[InterfaceCapability] = []
    definitions = (
        (InterfaceType.CO_SIMULATION, description.coSimulation),
        (InterfaceType.MODEL_EXCHANGE, description.modelExchange),
        (InterfaceType.SCHEDULED_EXECUTION, description.scheduledExecution),
    )

    for interface_type, interface in definitions:
        if interface is None:
            continue
        needs_tool = bool(interface.needsExecutionTool)
        can_execute = (
            interface_type is InterfaceType.CO_SIMULATION
            and current_platform in platforms
            and not needs_tool
        )
        result.append(
            InterfaceCapability(
                interface_type=interface_type,
                model_identifier=interface.modelIdentifier,
                can_execute=can_execute,
                needs_execution_tool=needs_tool,
                can_handle_variable_step=bool(
                    getattr(interface, "canHandleVariableCommunicationStepSize", False)
                ),
                supports_event_mode=bool(getattr(interface, "hasEventMode", False)),
                supports_early_return=bool(
                    getattr(interface, "canReturnEarlyAfterIntermediateUpdate", False)
                    or getattr(interface, "mightReturnEarlyFromDoStep", False)
                ),
            )
        )

    return tuple(result)


def _map_default_experiment(experiment: Any) -> DefaultExperiment:
    if experiment is None:
        return DefaultExperiment()
    return DefaultExperiment(
        start_time=_optional_float(experiment.startTime),
        stop_time=_optional_float(experiment.stopTime),
        tolerance=_optional_float(experiment.tolerance),
        step_size=_optional_float(experiment.stepSize),
    )


def _map_variable(variable: Any) -> VariableMetadata:
    return VariableMetadata(
        name=variable.name,
        value_reference=variable.valueReference,
        data_type=variable.type,
        causality=variable.causality,
        variability=variable.variability,
        initial=variable.initial,
        start=_typed_value(variable.start, variable.type, variable.shape),
        minimum=_typed_value(variable.min, variable.type),
        maximum=_typed_value(variable.max, variable.type),
        unit=variable.unit,
        display_unit=variable.displayUnit,
        description=variable.description,
        declared_type=getattr(variable.declaredType, "name", None),
        shape=tuple(variable.shape or ()),
    )


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


_ARRAY_START_BROADCAST_TYPES = {
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
    "Enumeration",
}


def _typed_value(value: Any, data_type: str, shape: tuple[int, ...] | None = None) -> Any:
    if value is None:
        return None

    values = str(value).split() if shape else (value,)

    def convert(item: Any) -> Any:
        if data_type in {"Real", "Float32", "Float64"}:
            return float(item)
        if data_type in {
            "Integer",
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
            return int(item)
        if data_type == "Boolean":
            return str(item).strip().lower() in {"true", "1"}
        return item

    converted = tuple(convert(item) for item in values)
    if not shape:
        return converted[0]

    resolved_shape = tuple(shape)
    if len(converted) == 1 and data_type in _ARRAY_START_BROADCAST_TYPES:
        converted *= array_size(resolved_shape)
    return reshape_array(converted, resolved_shape)


def _only_recoverable_validation_problems(problems: tuple[str, ...]) -> bool:
    return bool(problems) and all(
        problem.startswith('The unit "') and problem.endswith("is not defined.")
        for problem in problems
    )
