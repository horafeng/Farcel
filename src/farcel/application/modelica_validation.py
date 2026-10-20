"""Application-facing Modelica compilation validation and error mapping."""

from __future__ import annotations

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.modelica import ModelicaCompileRequest
from farcel.contracts.modelica_validation import (
    modelica_compile_request_issue_details,
    validate_modelica_compile_request,
)
from farcel.contracts.models import ValidationReport


def require_valid_modelica_compile_request(
    request: ModelicaCompileRequest,
) -> ValidationReport:
    """Map shared request-validation issues to Farcel's stable CONFIG_ERROR."""

    report = validate_modelica_compile_request(request)
    if not report.is_valid:
        raise EngineError(
            ErrorCode.CONFIG_ERROR,
            "Modelica 编译请求验证失败",
            {"issues": modelica_compile_request_issue_details(report)},
        )
    return report
