"""Pure validation for persistent Modelica build-provenance contracts."""

from __future__ import annotations

from collections.abc import Sequence

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.modelica_provenance import (
    ModelicaBuildProvenance,
    ModelicaSourceSnapshot,
)
from farcel.contracts.modelica_validation import is_supported_modelica_model_name
from farcel.contracts.models import ValidationIssue, ValidationReport
from farcel.contracts.project import ModelAsset
from farcel.contracts.project_paths import (
    is_lowercase_sha256,
    project_relative_path_issue,
)


def validate_modelica_build_provenance(
    provenance: ModelicaBuildProvenance,
    *,
    model_assets: Sequence[ModelAsset] | None = None,
) -> ValidationReport:
    """Return all pure semantic issues for one Modelica build record.

    The optional asset sequence is a caller-supplied Project view.  It only
    validates the stable ``fmu_asset_id`` relationship; it never accesses a
    project directory or asserts that either source or FMU currently exists.
    """

    issues: list[ValidationIssue] = []
    _validate_source_snapshot(provenance.source_snapshot, issues)
    _validate_nonempty(provenance.build_id, "build_id", "EMPTY_BUILD_ID", issues)
    _validate_model_name(provenance.model_name, issues)
    _validate_nonempty(
        provenance.compiler_name,
        "compiler_name",
        "EMPTY_COMPILER_NAME",
        issues,
    )
    _validate_nonempty(
        provenance.compiler_version,
        "compiler_version",
        "EMPTY_COMPILER_VERSION",
        issues,
    )
    _validate_compile_shape(provenance, issues)
    _validate_fmu_asset_reference(provenance.fmu_asset_id, model_assets, issues)
    return ValidationReport(tuple(issues))


def require_valid_modelica_build_provenance(
    provenance: ModelicaBuildProvenance,
    *,
    model_assets: Sequence[ModelAsset] | None = None,
) -> ValidationReport:
    """Return a valid report or raise Farcel's existing CONFIG_ERROR."""

    report = validate_modelica_build_provenance(
        provenance,
        model_assets=model_assets,
    )
    if not report.is_valid:
        raise EngineError(
            ErrorCode.CONFIG_ERROR,
            "Modelica 编译来源记录无效",
            {
                "issues": tuple(
                    {
                        "field": issue.field,
                        "code": issue.code,
                        "message": issue.message,
                    }
                    for issue in report.issues
                )
            },
        )
    return report


def _validate_source_snapshot(
    source_snapshot: object,
    issues: list[ValidationIssue],
) -> None:
    if not isinstance(source_snapshot, ModelicaSourceSnapshot):
        issues.append(
            ValidationIssue(
                "source_snapshot",
                "INVALID_SOURCE_SNAPSHOT",
                "source_snapshot 必须是 ModelicaSourceSnapshot",
            )
        )
        return

    path_issue = project_relative_path_issue(source_snapshot.relative_path)
    if path_issue is not None:
        issues.append(
            ValidationIssue(
                "source_snapshot.relative_path",
                path_issue.code,
                path_issue.message,
            )
        )
    if not is_lowercase_sha256(source_snapshot.sha256):
        issues.append(
            ValidationIssue(
                "source_snapshot.sha256",
                "SOURCE_SHA256_INVALID",
                "源文件 sha256 必须是 64 位小写十六进制",
            )
        )


def _validate_nonempty(
    value: object,
    field: str,
    code: str,
    issues: list[ValidationIssue],
) -> None:
    if not isinstance(value, str) or not value.strip():
        issues.append(ValidationIssue(field, code, f"{field} 不能为空"))


def _validate_model_name(value: object, issues: list[ValidationIssue]) -> None:
    if not isinstance(value, str) or not value.strip():
        issues.append(ValidationIssue("model_name", "EMPTY_MODEL_NAME", "model_name 不能为空"))
    elif not is_supported_modelica_model_name(value):
        issues.append(
            ValidationIssue(
                "model_name",
                "INVALID_MODEL_NAME",
                "model_name 只能使用未加引号的标识符和点分限定名",
            )
        )


def _validate_compile_shape(
    provenance: ModelicaBuildProvenance,
    issues: list[ValidationIssue],
) -> None:
    if provenance.fmi_version != "2.0":
        issues.append(
            ValidationIssue("fmi_version", "UNSUPPORTED_FMI_VERSION", "当前仅支持 FMI 2.0")
        )
    if provenance.fmu_type != "cs":
        issues.append(
            ValidationIssue("fmu_type", "UNSUPPORTED_FMU_TYPE", "当前仅支持 Co-Simulation")
        )


def _validate_fmu_asset_reference(
    asset_id: object,
    model_assets: Sequence[ModelAsset] | None,
    issues: list[ValidationIssue],
) -> None:
    if not isinstance(asset_id, str) or not asset_id.strip():
        issues.append(
            ValidationIssue("fmu_asset_id", "EMPTY_FMU_ASSET_ID", "fmu_asset_id 不能为空")
        )
        return
    if model_assets is None:
        return

    matches = [asset for asset in model_assets if asset.asset_id == asset_id]
    if not matches:
        issues.append(
            ValidationIssue(
                "fmu_asset_id",
                "UNKNOWN_FMU_ASSET_ID",
                "fmu_asset_id 未关联当前项目中的 ModelAsset",
            )
        )
    elif len(matches) > 1:
        issues.append(
            ValidationIssue(
                "fmu_asset_id",
                "AMBIGUOUS_FMU_ASSET_ID",
                "fmu_asset_id 在当前项目中存在多个同名 ModelAsset",
            )
        )
