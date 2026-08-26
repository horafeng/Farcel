from __future__ import annotations

from farcel.contracts.models import (
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    ValidationIssue,
    ValidationReport,
)


def validate_config(
    metadata: ModelMetadata, config: SimulationConfig
) -> ValidationReport:
    issues: list[ValidationIssue] = []

    if config.schema_version != "1.0":
        issues.append(
            ValidationIssue("schema_version", "unsupported", "仅支持配置版本 1.0")
        )
    if config.stop_time <= config.start_time:
        issues.append(
            ValidationIssue("stop_time", "range", "stop_time 必须大于 start_time")
        )
    if config.communication_step <= 0:
        issues.append(
            ValidationIssue("communication_step", "range", "通信步长必须大于 0")
        )
    if config.output_interval <= 0:
        issues.append(
            ValidationIssue("output_interval", "range", "输出间隔必须大于 0")
        )
    if InterfaceType.CO_SIMULATION not in metadata.interface_types:
        issues.append(
            ValidationIssue(
                "interface_type", "unsupported", "MVP 只执行 Co-Simulation FMU"
            )
        )

    known_variables = {variable.name for variable in metadata.variables}
    for output in config.selected_outputs:
        if output not in known_variables:
            issues.append(
                ValidationIssue(
                    "selected_outputs", "unknown_variable", f"未知输出变量: {output}"
                )
            )

    return ValidationReport(tuple(issues))

