"""Pure, implementation-independent validation for Modelica compilation contracts."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from farcel.contracts.modelica import ModelicaCompileRequest
from farcel.contracts.models import ValidationIssue, ValidationReport


_MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_MAX_TIMEOUT_SECONDS = 1800.0


def validate_modelica_compile_request(
    request: ModelicaCompileRequest,
) -> ValidationReport:
    """Return all local, side-effect-free issues for a compile request.

    Application orchestration and compiler adapters use this one rule set, so a
    direct adapter caller receives the same safe request boundary. This function
    never starts a process or writes files.
    """

    issues: list[ValidationIssue] = []
    _validate_source_path(request.source_path, issues)
    _validate_model_name(request.model_name, issues)
    _validate_output_directory(request.output_directory, issues)
    _validate_compiler_path(request.compiler_path, issues)
    _validate_options(request, issues)
    return ValidationReport(tuple(issues))


def modelica_compile_request_issue_details(
    report: ValidationReport,
) -> tuple[dict[str, str], ...]:
    """Convert a report to the stable CONFIG_ERROR details representation."""

    return tuple(
        {"field": issue.field, "code": issue.code, "message": issue.message}
        for issue in report.issues
    )


def is_supported_modelica_model_name(value: object) -> bool:
    """Return whether a value is in Farcel's first Modelica class-name subset."""

    return (
        isinstance(value, str)
        and bool(value.strip())
        and _MODEL_NAME_PATTERN.fullmatch(value) is not None
    )


def _validate_source_path(value: Any, issues: list[ValidationIssue]) -> None:
    path = _path_value(value, "source_path", issues)
    if path is None:
        return
    try:
        if not path.exists():
            issues.append(
                ValidationIssue("source_path", "SOURCE_FILE_NOT_FOUND", "Modelica 源文件不存在")
            )
        elif not path.is_file():
            issues.append(
                ValidationIssue("source_path", "SOURCE_PATH_NOT_FILE", "source_path 必须指向普通文件")
            )
        elif path.suffix.lower() != ".mo":
            issues.append(
                ValidationIssue("source_path", "UNSUPPORTED_SOURCE_EXTENSION", "当前仅支持 .mo 源文件")
            )
    except (OSError, ValueError):
        issues.append(
            ValidationIssue("source_path", "INVALID_SOURCE_PATH", "source_path 无法作为本地路径访问")
        )


def _validate_model_name(value: Any, issues: list[ValidationIssue]) -> None:
    if not isinstance(value, str) or not value.strip():
        issues.append(
            ValidationIssue("model_name", "EMPTY_MODEL_NAME", "model_name 不能为空")
        )
    elif not is_supported_modelica_model_name(value):
        issues.append(
            ValidationIssue(
                "model_name",
                "INVALID_MODEL_NAME",
                "model_name 只能使用未加引号的标识符和点分限定名",
            )
        )


def _validate_output_directory(value: Any, issues: list[ValidationIssue]) -> None:
    path = _path_value(value, "output_directory", issues)
    if path is None:
        return
    try:
        if not path.exists():
            issues.append(
                ValidationIssue(
                    "output_directory",
                    "OUTPUT_DIRECTORY_NOT_FOUND",
                    "输出目录必须已存在",
                )
            )
        elif not path.is_dir():
            issues.append(
                ValidationIssue(
                    "output_directory",
                    "OUTPUT_PATH_NOT_DIRECTORY",
                    "output_directory 必须指向目录",
                )
            )
    except (OSError, ValueError):
        issues.append(
            ValidationIssue(
                "output_directory",
                "INVALID_OUTPUT_DIRECTORY",
                "output_directory 无法作为本地路径访问",
            )
        )


def _validate_compiler_path(value: Any, issues: list[ValidationIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, (str, Path)):
        issues.append(
            ValidationIssue(
                "compiler_path", "INVALID_COMPILER_PATH", "compiler_path 必须是路径或 None"
            )
        )
    elif isinstance(value, str) and not value.strip():
        issues.append(
            ValidationIssue(
                "compiler_path", "EMPTY_COMPILER_PATH", "compiler_path 不能是空字符串"
            )
        )


def _validate_options(request: ModelicaCompileRequest, issues: list[ValidationIssue]) -> None:
    if request.fmi_version != "2.0":
        issues.append(
            ValidationIssue("fmi_version", "UNSUPPORTED_FMI_VERSION", "当前仅支持 FMI 2.0 编译")
        )
    if request.fmu_type != "cs":
        issues.append(
            ValidationIssue("fmu_type", "UNSUPPORTED_FMU_TYPE", "当前仅支持 Co-Simulation 编译")
        )
    timeout = request.timeout_seconds
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout))
    ):
        issues.append(
            ValidationIssue(
                "timeout_seconds", "INVALID_TIMEOUT_SECONDS", "timeout_seconds 必须是有限数值"
            )
        )
    elif timeout <= 0 or timeout > _MAX_TIMEOUT_SECONDS:
        issues.append(
            ValidationIssue(
                "timeout_seconds",
                "TIMEOUT_SECONDS_OUT_OF_RANGE",
                "timeout_seconds 必须大于 0 且不超过 1800 秒",
            )
        )


def _path_value(
    value: Any, field: str, issues: list[ValidationIssue]
) -> Path | None:
    if not isinstance(value, (str, Path)):
        issues.append(ValidationIssue(field, f"INVALID_{field.upper()}", f"{field} 必须是路径"))
        return None
    if isinstance(value, str) and not value.strip():
        issues.append(ValidationIssue(field, f"EMPTY_{field.upper()}", f"{field} 不能为空"))
        return None
    try:
        return Path(value)
    except (OSError, TypeError, ValueError):
        issues.append(ValidationIssue(field, f"INVALID_{field.upper()}", f"{field} 无法作为路径处理"))
        return None
