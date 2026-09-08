"""Thin project-case orchestration over the existing graph execution API."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from farcel.application.project_assets import ProjectAssetValidator
from farcel.application.project_validation import ProjectValidator
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.graph import (
    GraphSimulationConfig,
    GraphSimulationResult,
    ModelNode,
    SimulationGraph,
)
from farcel.contracts.models import RunProgress, ValidationIssue, ValidationReport
from farcel.contracts.project import ModelAsset, SimulationCase, SimulationProject
from farcel.contracts.run_control import RunControl


class GraphRunCallable(Protocol):
    """The existing graph execution entry point supplied by composition code."""

    def __call__(
        self,
        graph: SimulationGraph,
        config: GraphSimulationConfig,
        *,
        control: RunControl | None = None,
        on_progress: Callable[[RunProgress], None] | None = None,
    ) -> GraphSimulationResult: ...


class ProjectService:
    """Validate and execute exactly one persisted project case."""

    def __init__(
        self,
        validator: ProjectValidator,
        run_graph: GraphRunCallable,
        asset_validator: ProjectAssetValidator | None = None,
    ) -> None:
        self._validator = validator
        self._run_graph = run_graph
        self._asset_validator = asset_validator or ProjectAssetValidator()

    def run_case(
        self,
        project_root: Path,
        project: SimulationProject,
        case_id: str,
        *,
        control: RunControl | None = None,
        on_progress: Callable[[RunProgress], None] | None = None,
    ) -> GraphSimulationResult:
        report = self._validator.validate(project_root, project)
        if not report.is_valid:
            raise _configuration_error("Project 验证失败", report)

        case = _find_case(project.simulation_cases, case_id)
        if case is None:
            raise _configuration_error(
                "请求的 SimulationCase 不存在",
                ValidationReport((ValidationIssue("case_id", "UNKNOWN_CASE_ID", "case_id 不存在"),)),
            )

        graph = self._materialize_target_graph(project_root, project, case)
        return self._run_graph(
            graph,
            case.config,
            control=control,
            on_progress=on_progress,
        )

    def _materialize_target_graph(
        self,
        project_root: Path,
        project: SimulationProject,
        case: SimulationCase,
    ) -> SimulationGraph:
        assets: dict[str, tuple[int, ModelAsset]] = {}
        resolved_paths: dict[str, Path] = {}
        checked_paths: set[str] = set()
        issues: list[ValidationIssue] = []

        for index, asset in enumerate(project.model_assets):
            if asset.relative_path in assets:
                issues.append(
                    ValidationIssue(
                        f"model_assets[{index}].relative_path",
                        "DUPLICATE_ASSET_PATH",
                        "已验证的 project 包含重复 ModelAsset.relative_path",
                    )
                )
            else:
                assets[asset.relative_path] = (index, asset)

        for node in case.graph.nodes:
            asset_entry = assets.get(node.model_path)
            if asset_entry is None:
                issues.append(
                    ValidationIssue(
                        "case_id",
                        "UNREGISTERED_MODEL_ASSET",
                        "已验证的 case 缺少注册 ModelAsset",
                    )
                )
                continue
            asset_index, asset = asset_entry
            if node.model_path in checked_paths:
                continue
            checked_paths.add(node.model_path)
            check = self._asset_validator.check(project_root, asset)
            if not check.report.is_valid:
                issues.extend(
                    _prefix_asset_issue(asset_index, issue) for issue in check.report.issues
                )
                continue
            if check.resolved_path is None:
                issues.append(
                    ValidationIssue(
                        f"model_assets[{asset_index}].relative_path",
                        "PROJECT_PATH_INVALID",
                        "ModelAsset 未返回 resolved_path",
                    )
                )
                continue
            resolved_paths[node.model_path] = check.resolved_path

        if issues:
            raise _configuration_error("目标 case 的 ModelAsset 验证失败", ValidationReport(tuple(issues)))

        return SimulationGraph(
            nodes=tuple(
                ModelNode(node.node_id, str(resolved_paths[node.model_path]), node.config)
                for node in case.graph.nodes
            ),
            connections=case.graph.connections,
        )


def _find_case(cases: tuple[SimulationCase, ...], case_id: str) -> SimulationCase | None:
    return next((case for case in cases if case.case_id == case_id), None)


def _prefix_asset_issue(index: int, issue: ValidationIssue) -> ValidationIssue:
    return ValidationIssue(
        f"model_assets[{index}].{issue.field}", issue.code, issue.message
    )


def _configuration_error(message: str, report: ValidationReport) -> EngineError:
    return EngineError(
        ErrorCode.CONFIG_ERROR,
        message,
        {
            "issues": tuple(
                {"field": issue.field, "code": issue.code, "message": issue.message}
                for issue in report.issues
            )
        },
    )
