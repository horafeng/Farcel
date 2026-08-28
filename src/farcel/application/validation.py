from __future__ import annotations

import math
from typing import Any

from farcel.contracts.errors import ErrorCode
from farcel.contracts.models import (
    InputUpdate,
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    ValidationIssue,
    ValidationReport,
    VariableMetadata,
)


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

    co_simulation = next(
        (
            capability
            for capability in metadata.interface_capabilities
            if capability.interface_type is InterfaceType.CO_SIMULATION
        ),
        None,
    )
    if InterfaceType.CO_SIMULATION not in metadata.interface_types:
        issues.append(
            ValidationIssue(
                "model",
                ErrorCode.UNSUPPORTED_INTERFACE.value,
                "FMU 可以解析，但 Farcel 当前只执行 Co-Simulation FMU",
            )
        )
    elif not metadata.capabilities.can_execute or (
        metadata.executable_interface is not InterfaceType.CO_SIMULATION
    ):
        if co_simulation is not None and co_simulation.needs_execution_tool:
            code = ErrorCode.UNSUPPORTED_INTERFACE.value
            message = "FMU 可以解析，但需要 Farcel 当前不支持的外部执行工具"
        else:
            code = ErrorCode.PLATFORM_BINARY_MISSING.value
            message = "FMU 可以解析，但缺少当前平台可执行的 Co-Simulation 二进制"
        issues.append(ValidationIssue("model", code, message))

    known_variables = {variable.name: variable for variable in metadata.variables}
    for name, value in config.parameters.items():
        variable = known_variables.get(name)
        if variable is None:
            issues.append(
                ValidationIssue(
                    "parameters", "UNKNOWN_PARAMETER", f"未知参数变量: {name}"
                )
            )
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
        parameter_issue = _validate_parameter_value(name, value, variable)
        if parameter_issue is not None:
            issues.append(parameter_issue)

    for name, value in config.initial_inputs.items():
        input_issue = _validate_input_value(
            name, value, known_variables, "initial_inputs"
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
            input_issue = _validate_input_value(name, value, known_variables, field)
            if input_issue is not None:
                issues.append(input_issue)

    for output in config.selected_outputs:
        if output not in known_variables:
            issues.append(
                ValidationIssue(
                    "selected_outputs", "UNKNOWN_OUTPUT", f"未知输出变量: {output}"
                )
            )

    return ValidationReport(tuple(issues))


def resolve_output_interval(config: SimulationConfig) -> float:
    """Return the effective result-sampling interval for a valid configuration."""
    return (
        config.communication_step
        if config.output_interval is None
        else config.output_interval
    )


def _validate_parameter_value(
    name: str, value: Any, variable: VariableMetadata
) -> ValidationIssue | None:
    if variable.shape:
        return ValidationIssue(
            "parameters",
            "UNSUPPORTED_PARAMETER_TYPE",
            f"本阶段暂不支持数组参数覆盖: {name}",
        )

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
    return _validate_scalar_value(name, value, variable, field, "INPUT")


def _validate_scalar_value(
    name: str,
    value: Any,
    variable: VariableMetadata,
    field: str,
    code_prefix: str,
) -> ValidationIssue | None:
    if variable.shape:
        return ValidationIssue(
            field,
            f"UNSUPPORTED_{code_prefix}_TYPE",
            f"本阶段暂不支持数组 input: {name}",
        )

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
