from __future__ import annotations

import math
from typing import Any

from farcel.contracts._arrays import (
    ArrayShapeError,
    EffectiveShapeError,
    flatten_array,
    resolve_effective_shape,
)
from farcel.contracts.errors import ErrorCode
from farcel.contracts.models import (
    InterfaceCapability,
    InputUpdate,
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    ValidationIssue,
    ValidationReport,
    VariableMetadata,
)


def resolve_execution_interface(
    metadata: ModelMetadata, config: SimulationConfig
) -> InterfaceType | None:
    """Resolve the preferred public execution interface for a valid config."""

    requested = config.execution_interface
    if isinstance(requested, InterfaceType):
        return requested
    for interface_type in (InterfaceType.CO_SIMULATION, InterfaceType.MODEL_EXCHANGE):
        capability = _interface_capability(metadata, interface_type)
        if capability is not None and capability.can_execute:
            return interface_type
    return None


def validate_config(
    metadata: ModelMetadata, config: SimulationConfig
) -> ValidationReport:
    issues: list[ValidationIssue] = []

    if config.schema_version != "1.0":
        issues.append(
            ValidationIssue(
                "schema_version", "UNSUPPORTED_SCHEMA", "仅支持配置版本 1.0"
            )
        )
    if not _is_finite_number(config.start_time) or not _is_finite_number(
        config.stop_time
    ):
        issues.append(
            ValidationIssue(
                "time", "INVALID_TIME_VALUE", "start_time 和 stop_time 必须是有限数值"
            )
        )
    elif config.start_time >= config.stop_time:
        issues.append(
            ValidationIssue(
                "stop_time",
                "INVALID_TIME_RANGE",
                "start_time 必须小于 stop_time",
            )
        )
    if not _is_finite_number(config.communication_step) or config.communication_step <= 0:
        issues.append(
            ValidationIssue(
                "communication_step",
                "INVALID_STEP_SIZE",
                "communication step size 必须是大于 0 的有限数值",
            )
        )
    if config.output_interval is not None and (
        not _is_finite_number(config.output_interval) or config.output_interval <= 0
    ):
        issues.append(
            ValidationIssue(
                "output_interval",
                "INVALID_OUTPUT_INTERVAL",
                "输出间隔必须是大于 0 的有限数值",
            )
        )
    elif (
        config.output_interval is not None
        and _is_finite_number(config.communication_step)
    ):
        sample_step_ratio = config.output_interval / config.communication_step
        if not math.isclose(
            sample_step_ratio,
            round(sample_step_ratio),
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            issues.append(
                ValidationIssue(
                    "output_interval",
                    "OUTPUT_INTERVAL_NOT_COMMUNICATION_ALIGNED",
                    "输出间隔必须是 communication_step 的整数倍，以保证采样位于 communication point",
                )
            )

    requested_interface = config.execution_interface
    if requested_interface is not None and not isinstance(requested_interface, InterfaceType):
        issues.append(
            ValidationIssue(
                "execution_interface",
                "INVALID_EXECUTION_INTERFACE",
                "execution_interface 必须是 InterfaceType 或 None",
            )
        )
    elif requested_interface is InterfaceType.SCHEDULED_EXECUTION:
        issues.append(
            ValidationIssue(
                "execution_interface",
                ErrorCode.UNSUPPORTED_INTERFACE.value,
                "Scheduled Execution runtime 尚未在当前里程碑启用",
            )
        )

    if requested_interface is None or isinstance(requested_interface, InterfaceType):
        _validate_execution_interface(metadata, config, issues)

    known_variables = {variable.name: variable for variable in metadata.variables}
    effective_shapes = _resolve_effective_shapes(
        metadata, config, known_variables, issues
    )
    for name, value in config.parameters.items():
        variable = known_variables.get(name)
        if variable is None:
            issues.append(
                ValidationIssue(
                    "parameters", "UNKNOWN_PARAMETER", f"未知参数变量: {name}"
                )
            )
            continue
        if metadata.fmi_version == "3.0" and variable.causality == "structuralParameter":
            continue
        if variable.causality not in {"parameter", "structuralParameter"}:
            issues.append(
                ValidationIssue(
                    "parameters",
                    "INVALID_PARAMETER_CAUSALITY",
                    f"变量不是可覆盖参数: {name}",
                )
            )
            continue
        parameter_issue = _validate_parameter_value(
            name, value, variable, effective_shapes.get(name, variable.shape)
        )
        if parameter_issue is not None:
            issues.append(parameter_issue)

    for name, value in config.initial_inputs.items():
        input_issue = _validate_input_value(
            name, value, known_variables, "initial_inputs", effective_shapes
        )
        if input_issue is not None:
            issues.append(input_issue)

    previous_update_time: float | None = None
    for index, update in enumerate(config.input_schedule):
        field = f"input_schedule[{index}]"
        if not isinstance(update, InputUpdate):
            issues.append(
                ValidationIssue(
                    field,
                    "INVALID_INPUT_UPDATE",
                    "input_schedule 只能包含 InputUpdate",
                )
            )
            continue
        if not _is_finite_number(update.time):
            issues.append(
                ValidationIssue(field, "INVALID_INPUT_TIME", "input update time 必须是有限数值")
            )
        elif not (config.start_time <= update.time < config.stop_time):
            issues.append(
                ValidationIssue(
                    field,
                    "INPUT_TIME_OUT_OF_RANGE",
                    "input update time 必须位于 [start_time, stop_time) 内",
                )
            )
        elif previous_update_time is not None and update.time <= previous_update_time:
            issues.append(
                ValidationIssue(
                    field,
                    "INPUT_TIMES_NOT_INCREASING",
                    "input_schedule 时间必须严格递增",
                )
            )
        elif _is_finite_number(config.communication_step):
            step_index = (update.time - config.start_time) / config.communication_step
            if not math.isclose(step_index, round(step_index), rel_tol=0.0, abs_tol=1e-9):
                issues.append(
                    ValidationIssue(
                        field,
                        "INPUT_TIME_NOT_COMMUNICATION_POINT",
                        "input update time 必须与 communication point 对齐",
                    )
                )
        if _is_finite_number(update.time):
            previous_update_time = float(update.time)
        for name, value in update.values.items():
            input_issue = _validate_input_value(
                name, value, known_variables, field, effective_shapes
            )
            if input_issue is not None:
                issues.append(input_issue)

    for output in config.selected_outputs:
        variable = known_variables.get(output)
        if variable is None:
            issues.append(
                ValidationIssue(
                    "selected_outputs", "UNKNOWN_OUTPUT", f"未知输出变量: {output}"
                )
            )
        elif (
            metadata.fmi_version == "3.0"
            and variable.data_type.lower() in {"binary", "clock"}
        ):
            issues.append(
                ValidationIssue(
                    "selected_outputs",
                    "UNSUPPORTED_OUTPUT_TYPE",
                    f"本阶段不支持读取 {variable.data_type} output: {output}",
                )
            )

    return ValidationReport(tuple(issues))


def _validate_execution_interface(
    metadata: ModelMetadata, config: SimulationConfig, issues: list[ValidationIssue]
) -> None:
    requested = config.execution_interface
    if requested is InterfaceType.SCHEDULED_EXECUTION:
        return
    effective = resolve_execution_interface(metadata, config)
    if requested is None:
        if effective is not None:
            return
        declared_me = _interface_capability(metadata, InterfaceType.MODEL_EXCHANGE)
        declared_cs = _interface_capability(metadata, InterfaceType.CO_SIMULATION)
        if declared_me is not None and metadata.fmi_version != "2.0" and declared_cs is None:
            message = "FMU 包含 Model Exchange，但 Farcel 当前仅公开支持 FMI 2.0 Model Exchange"
            code = ErrorCode.UNSUPPORTED_INTERFACE.value
        elif any(
            capability is not None and capability.needs_execution_tool
            for capability in (declared_cs, declared_me)
        ):
            message = "FMU 可以解析，但需要 Farcel 当前不支持的外部执行工具"
            code = ErrorCode.UNSUPPORTED_INTERFACE.value
        elif declared_cs is None and declared_me is None:
            message = "FMU 可以解析，但当前不包含 Farcel 可执行接口"
            code = ErrorCode.UNSUPPORTED_INTERFACE.value
        else:
            message = "FMU 可以解析，但缺少当前平台对应接口的可执行二进制"
            code = ErrorCode.PLATFORM_BINARY_MISSING.value
        issues.append(ValidationIssue("model", code, message))
        return

    capability = _interface_capability(metadata, requested)
    if capability is None:
        issues.append(
            ValidationIssue("execution_interface", ErrorCode.UNSUPPORTED_INTERFACE.value, "FMU 未声明所请求的执行接口")
        )
    elif requested is InterfaceType.MODEL_EXCHANGE and metadata.fmi_version != "2.0":
        issues.append(
            ValidationIssue("execution_interface", ErrorCode.UNSUPPORTED_INTERFACE.value, "Farcel 当前仅公开支持 FMI 2.0 Model Exchange")
        )
    elif capability.needs_execution_tool:
        issues.append(
            ValidationIssue("execution_interface", ErrorCode.UNSUPPORTED_INTERFACE.value, "FMU 需要 Farcel 当前不支持的外部执行工具")
        )
    elif not capability.can_execute:
        issues.append(
            ValidationIssue("execution_interface", ErrorCode.PLATFORM_BINARY_MISSING.value, "FMU 缺少当前平台所请求接口的可执行二进制")
        )


def _interface_capability(
    metadata: ModelMetadata, interface_type: InterfaceType
) -> InterfaceCapability | None:
    capability = next(
        (item for item in metadata.interface_capabilities if item.interface_type is interface_type),
        None,
    )
    if capability is not None:
        return capability
    if interface_type not in metadata.interface_types:
        return None
    return InterfaceCapability(
        interface_type=interface_type,
        can_execute=(
            metadata.capabilities.can_execute
            and metadata.executable_interface is interface_type
        ),
    )


def resolve_output_interval(config: SimulationConfig) -> float:
    """Return the effective result-sampling interval for a valid configuration."""
    return (
        config.communication_step
        if config.output_interval is None
        else config.output_interval
    )


def _resolve_effective_shapes(
    metadata: ModelMetadata,
    config: SimulationConfig,
    known_variables: dict[str, VariableMetadata],
    issues: list[ValidationIssue],
) -> dict[str, tuple[int, ...]]:
    structural_values: dict[int, Any] = {}
    if metadata.fmi_version == "3.0":
        for variable in metadata.variables:
            if variable.causality == "structuralParameter":
                structural_values[variable.value_reference] = variable.start

        for name, value in config.parameters.items():
            variable = known_variables.get(name)
            if variable is None or variable.causality != "structuralParameter":
                continue
            issue = _validate_structural_parameter_value(name, value, variable)
            if issue is not None:
                issues.append(issue)
                continue
            structural_values[variable.value_reference] = value

    effective_shapes: dict[str, tuple[int, ...]] = {}
    for variable in metadata.variables:
        try:
            effective_shapes[variable.name] = resolve_effective_shape(
                variable.shape,
                variable.dimension_value_references,
                structural_values,
            )
        except EffectiveShapeError:
            issues.append(
                ValidationIssue(
                    "model",
                    "INVALID_DIMENSION_VALUE",
                    f"变量 {variable.name} 的 dynamic dimension 无法解析",
                )
            )
            effective_shapes[variable.name] = variable.shape
    return effective_shapes


def _validate_structural_parameter_value(
    name: str, value: Any, variable: VariableMetadata
) -> ValidationIssue | None:
    if variable.shape:
        return ValidationIssue(
            "parameters",
            "UNSUPPORTED_STRUCTURAL_PARAMETER_TYPE",
            f"当前阶段不支持数组 structural parameter override: {name}",
        )
    data_type = variable.data_type.lower()
    if data_type not in {
        "integer", "int8", "uint8", "int16", "uint16", "int32", "uint32",
        "int64", "uint64", "enumeration",
    }:
        return ValidationIssue(
            "parameters",
            "UNSUPPORTED_STRUCTURAL_PARAMETER_TYPE",
            f"当前阶段不支持 {variable.data_type} structural parameter override: {name}",
        )
    if not _valid_scalar_type(value, data_type):
        return ValidationIssue(
            "parameters",
            "INVALID_PARAMETER_TYPE",
            f"structural parameter {name} 的值与 {variable.data_type} 类型不匹配",
        )
    integer_range = _INTEGER_TYPE_RANGES.get(data_type)
    if integer_range is not None and not integer_range[0] <= value <= integer_range[1]:
        return ValidationIssue(
            "parameters",
            "PARAMETER_OUT_OF_TYPE_RANGE",
            f"structural parameter {name} 超出 {variable.data_type} 的标量范围",
        )
    if variable.minimum is not None and value < variable.minimum:
        return ValidationIssue(
            "parameters",
            "PARAMETER_BELOW_MINIMUM",
            f"参数 {name} 小于允许的最小值 {variable.minimum}",
        )
    if variable.maximum is not None and value > variable.maximum:
        return ValidationIssue(
            "parameters",
            "PARAMETER_ABOVE_MAXIMUM",
            f"参数 {name} 大于允许的最大值 {variable.maximum}",
        )
    return None


def _validate_parameter_value(
    name: str,
    value: Any,
    variable: VariableMetadata,
    shape: tuple[int, ...],
) -> ValidationIssue | None:
    if variable.shape:
        return _validate_array_value(name, value, variable, "parameters", shape)

    data_type = variable.data_type.lower()
    if data_type in {"real", "float32", "float64"}:
        valid_type = _is_finite_number(value)
    elif data_type in {
        "integer",
        "int8",
        "uint8",
        "int16",
        "uint16",
        "int32",
        "uint32",
        "int64",
        "uint64",
        "enumeration",
    }:
        valid_type = isinstance(value, int) and not isinstance(value, bool)
    elif data_type == "boolean":
        valid_type = isinstance(value, bool)
    elif data_type == "string":
        valid_type = isinstance(value, str)
    else:
        return ValidationIssue(
            "parameters",
            "UNSUPPORTED_PARAMETER_TYPE",
            f"本阶段不支持覆盖 {variable.data_type} 参数: {name}",
        )

    if not valid_type:
        return ValidationIssue(
            "parameters",
            "INVALID_PARAMETER_TYPE",
            f"参数 {name} 的值与 {variable.data_type} 类型不匹配",
        )

    if variable.minimum is not None and value < variable.minimum:
        return ValidationIssue(
            "parameters",
            "PARAMETER_BELOW_MINIMUM",
            f"参数 {name} 小于允许的最小值 {variable.minimum}",
        )
    if variable.maximum is not None and value > variable.maximum:
        return ValidationIssue(
            "parameters",
            "PARAMETER_ABOVE_MAXIMUM",
            f"参数 {name} 大于允许的最大值 {variable.maximum}",
        )
    return None


def _validate_input_value(
    name: str,
    value: Any,
    known_variables: dict[str, VariableMetadata],
    field: str,
    effective_shapes: dict[str, tuple[int, ...]],
) -> ValidationIssue | None:
    variable = known_variables.get(name)
    if variable is None:
        return ValidationIssue(field, "UNKNOWN_INPUT", f"未知 input 变量: {name}")
    if variable.causality != "input":
        return ValidationIssue(
            field,
            "INVALID_INPUT_CAUSALITY",
            f"变量不是可写 input: {name}",
        )
    return _validate_scalar_value(
        name, value, variable, field, "INPUT", effective_shapes.get(name, variable.shape)
    )


def _validate_scalar_value(
    name: str,
    value: Any,
    variable: VariableMetadata,
    field: str,
    code_prefix: str,
    shape: tuple[int, ...],
) -> ValidationIssue | None:
    if variable.shape:
        return _validate_array_value(name, value, variable, field, shape)

    data_type = variable.data_type.lower()
    if data_type in {"real", "float32", "float64"}:
        valid_type = _is_finite_number(value)
    elif data_type in {
        "integer", "int8", "uint8", "int16", "uint16", "int32", "uint32",
        "int64", "uint64", "enumeration",
    }:
        valid_type = isinstance(value, int) and not isinstance(value, bool)
    elif data_type == "boolean":
        valid_type = isinstance(value, bool)
    elif data_type == "string":
        valid_type = isinstance(value, str)
    else:
        return ValidationIssue(
            field,
            f"UNSUPPORTED_{code_prefix}_TYPE",
            f"本阶段不支持 {variable.data_type} input: {name}",
        )

    if not valid_type:
        return ValidationIssue(
            field,
            f"INVALID_{code_prefix}_TYPE",
            f"input {name} 的值与 {variable.data_type} 类型不匹配",
        )

    integer_range = _INTEGER_TYPE_RANGES.get(data_type)
    if integer_range is not None and not integer_range[0] <= value <= integer_range[1]:
        return ValidationIssue(
            field,
            f"{code_prefix}_OUT_OF_TYPE_RANGE",
            f"input {name} 超出 {variable.data_type} 的标量范围",
        )
    if variable.minimum is not None and value < variable.minimum:
        return ValidationIssue(
            field,
            f"{code_prefix}_BELOW_MINIMUM",
            f"input {name} 小于允许的最小值 {variable.minimum}",
        )
    if variable.maximum is not None and value > variable.maximum:
        return ValidationIssue(
            field,
            f"{code_prefix}_ABOVE_MAXIMUM",
            f"input {name} 大于允许的最大值 {variable.maximum}",
        )
    return None


def _validate_array_value(
    name: str,
    value: Any,
    variable: VariableMetadata,
    field: str,
    shape: tuple[int, ...],
) -> ValidationIssue | None:
    try:
        elements = flatten_array(value, shape)
    except ArrayShapeError:
        return ValidationIssue(
            field,
            "INVALID_ARRAY_SHAPE",
            f"数组变量 {name} 的值必须匹配 shape {shape}",
        )

    data_type = variable.data_type.lower()
    for element in elements:
        if not _valid_scalar_type(element, data_type):
            return ValidationIssue(
                field,
                "INVALID_ARRAY_ELEMENT_TYPE",
                f"数组变量 {name} 的元素与 {variable.data_type} 类型不匹配",
            )
        integer_range = _INTEGER_TYPE_RANGES.get(data_type)
        if integer_range is not None and not integer_range[0] <= element <= integer_range[1]:
            return ValidationIssue(
                field,
                "INVALID_ARRAY_ELEMENT_TYPE",
                f"数组变量 {name} 的元素超出 {variable.data_type} 类型范围",
            )
        if variable.minimum is not None and element < variable.minimum:
            return ValidationIssue(
                field,
                "ARRAY_ELEMENT_BELOW_MINIMUM",
                f"数组变量 {name} 的元素小于允许的最小值 {variable.minimum}",
            )
        if variable.maximum is not None and element > variable.maximum:
            return ValidationIssue(
                field,
                "ARRAY_ELEMENT_ABOVE_MAXIMUM",
                f"数组变量 {name} 的元素大于允许的最大值 {variable.maximum}",
            )
    return None


def _valid_scalar_type(value: Any, data_type: str) -> bool:
    if data_type in {"real", "float32", "float64"}:
        return _is_finite_number(value)
    if data_type in {
        "integer",
        "int8",
        "uint8",
        "int16",
        "uint16",
        "int32",
        "uint32",
        "int64",
        "uint64",
        "enumeration",
    }:
        return isinstance(value, int) and not isinstance(value, bool)
    if data_type == "boolean":
        return isinstance(value, bool)
    if data_type == "string":
        return isinstance(value, str)
    return False


_INTEGER_TYPE_RANGES = {
    "int8": (-(2**7), 2**7 - 1),
    "uint8": (0, 2**8 - 1),
    "int16": (-(2**15), 2**15 - 1),
    "uint16": (0, 2**16 - 1),
    "int32": (-(2**31), 2**31 - 1),
    "uint32": (0, 2**32 - 1),
    "int64": (-(2**63), 2**63 - 1),
    "uint64": (0, 2**64 - 1),
}


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )
