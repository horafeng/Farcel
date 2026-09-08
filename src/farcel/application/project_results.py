"""Build portable project-run provenance without filesystem access."""

from __future__ import annotations

from copy import deepcopy

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.graph import GraphSimulationResult
from farcel.contracts.models import ValidationIssue
from farcel.contracts.project import ModelAsset, SimulationCase, SimulationProject
from farcel.contracts.project_result import ProjectAssetSnapshot, ProjectRunArtifact


def build_project_run_artifact(
    project: SimulationProject,
    case_id: str,
    run_id: str,
    result: GraphSimulationResult,
) -> ProjectRunArtifact:
    if not isinstance(run_id, str) or not run_id.strip():
        raise _configuration_error("run_id", "EMPTY_RUN_ID", "run_id 不能为空")

    case = next((item for item in project.simulation_cases if item.case_id == case_id), None)
    if case is None:
        raise _configuration_error("case_id", "UNKNOWN_CASE_ID", "case_id 不存在")

    assets = _assets_by_relative_path(project.model_assets)
    snapshots: list[ProjectAssetSnapshot] = []
    seen_paths: set[str] = set()
    for node in case.graph.nodes:
        if node.model_path in seen_paths:
            continue
        seen_paths.add(node.model_path)
        matches = assets.get(node.model_path, ())
        if not matches:
            raise _configuration_error(
                "case_id", "UNREGISTERED_MODEL_ASSET", "case 引用了未注册 ModelAsset"
            )
        if len(matches) > 1:
            raise _configuration_error(
                "case_id", "AMBIGUOUS_MODEL_ASSET", "case ModelAsset.relative_path 不唯一"
            )
        asset = matches[0]
        snapshots.append(
            ProjectAssetSnapshot(asset.asset_id, asset.relative_path, asset.sha256)
        )

    return ProjectRunArtifact(
        run_id=run_id,
        project_id=project.project_id,
        case_snapshot=deepcopy(case),
        asset_snapshots=tuple(snapshots),
        result=deepcopy(result),
    )


def _assets_by_relative_path(
    assets: tuple[ModelAsset, ...],
) -> dict[str, tuple[ModelAsset, ...]]:
    grouped: dict[str, list[ModelAsset]] = {}
    for asset in assets:
        grouped.setdefault(asset.relative_path, []).append(asset)
    return {path: tuple(matches) for path, matches in grouped.items()}


def _configuration_error(field: str, code: str, message: str) -> EngineError:
    issue = ValidationIssue(field, code, message)
    return EngineError(
        ErrorCode.CONFIG_ERROR,
        "Project run provenance 构建失败",
        {"issues": ({"field": issue.field, "code": issue.code, "message": issue.message},)},
    )
