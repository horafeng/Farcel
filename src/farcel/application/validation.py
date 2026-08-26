from __future__ import annotations

import math
from typing import Any

from farcel.contracts.errors import ErrorCode
from farcel.contracts.models import (
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
    if not _is_finite_number(config.output_interval) or config.output_interval <= 0:
        issues.append(
            ValidationIssue(
                "output_interval",
                "INVALID_OUTPUT_INTERVAL",
                "输出间隔必须是大于 0 的有限数值",
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

    for output in config.selected_outputs:
        if output not in known_variables:
            issues.append(
                ValidationIssue(
                    "selected_outputs", "UNKNOWN_OUTPUT", f"未知输出变量: {output}"
                )
            )

    return ValidationReport(tuple(issues))


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


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )
