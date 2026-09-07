"""Project-level validation that reuses asset and graph validation boundaries."""

from __future__ import annotations

from pathlib import Path

from farcel.application.graph_validation import GraphValidator
from farcel.application.project_assets import ProjectAssetValidator
from farcel.contracts.graph import ModelNode, SimulationGraph
from farcel.contracts.models import ValidationIssue, ValidationReport
from farcel.contracts.ports import ModelImporter
from farcel.contracts.project import PROJECT_SCHEMA_VERSION, SimulationProject


class ProjectValidator:
    """Validate one project without persistence or simulation execution."""

    def __init__(
        self,
        importer: ModelImporter,
        asset_validator: ProjectAssetValidator | None = None,
    ) -> None:
        self._asset_validator = asset_validator or ProjectAssetValidator()
        self._graph_validator = GraphValidator(importer)

    def validate(self, project_root: Path, project: SimulationProject) -> ValidationReport:
        issues: list[ValidationIssue] = []
        self._validate_project_identity(project, issues)
        asset_paths = self._validate_assets(project_root, project, issues)
        case_ids = self._validate_case_ids(project, issues)
        self._validate_run_history(project, case_ids, issues)

        for case_index, case in enumerate(project.simulation_cases):
            resolved_paths = self._resolve_case_nodes(
                case_index, case.graph, asset_paths, issues
            )
            if resolved_paths is None:
                continue
            resolved_graph = _resolved_graph(case.graph, resolved_paths)
            graph_report = self._graph_validator.validate(resolved_graph, case.config)
            issues.extend(_prefix_graph_issue(case_index, issue) for issue in graph_report.issues)

        return ValidationReport(tuple(issues))

    @staticmethod
    def _validate_project_identity(
        project: SimulationProject, issues: list[ValidationIssue]
    ) -> None:
        if project.schema_version != PROJECT_SCHEMA_VERSION:
            issues.append(
                ValidationIssue(
                    "schema_version",
                    "UNSUPPORTED_PROJECT_SCHEMA",
                    f"仅支持 project schema {PROJECT_SCHEMA_VERSION}",
                )
            )
        if _is_blank(project.project_id):
            issues.append(
                ValidationIssue("project_id", "EMPTY_PROJECT_ID", "project_id 不能为空")
            )

    def _validate_assets(
        self,
        project_root: Path,
        project: SimulationProject,
        issues: list[ValidationIssue],
    ) -> dict[str, list[Path | None]]:
        asset_ids: dict[str, int] = {}
        asset_path_indexes: dict[str, list[int]] = {}
        checks = []
        for index, asset in enumerate(project.model_assets):
            field = f"model_assets[{index}]"
            if _is_blank(asset.asset_id):
                issues.append(
                    ValidationIssue(f"{field}.asset_id", "EMPTY_ASSET_ID", "asset_id 不能为空")
                )
            elif asset.asset_id in asset_ids:
                issues.append(
                    ValidationIssue(
                        f"{field}.asset_id", "DUPLICATE_ASSET_ID", "asset_id 必须唯一"
                    )
                )
            else:
                asset_ids[asset.asset_id] = index

            if isinstance(asset.relative_path, str):
                previous = asset_path_indexes.setdefault(asset.relative_path, [])
                if previous:
                    issues.append(
                        ValidationIssue(
                            f"{field}.relative_path",
                            "DUPLICATE_ASSET_PATH",
                            "relative_path 必须唯一",
                        )
                    )
                previous.append(index)

            check = self._asset_validator.check(project_root, asset)
            checks.append(check)
            issues.extend(_prefix_issue(field, issue) for issue in check.report.issues)

        return {
            path: [checks[index].resolved_path for index in indexes]
            for path, indexes in asset_path_indexes.items()
        }

    @staticmethod
    def _validate_case_ids(
        project: SimulationProject, issues: list[ValidationIssue]
    ) -> set[str]:
        case_ids: set[str] = set()
        for index, case in enumerate(project.simulation_cases):
            field = f"simulation_cases[{index}].case_id"
            if _is_blank(case.case_id):
                issues.append(ValidationIssue(field, "EMPTY_CASE_ID", "case_id 不能为空"))
            elif case.case_id in case_ids:
                issues.append(ValidationIssue(field, "DUPLICATE_CASE_ID", "case_id 必须唯一"))
            else:
                case_ids.add(case.case_id)
        return case_ids

    @staticmethod
    def _validate_run_history(
        project: SimulationProject,
        case_ids: set[str],
        issues: list[ValidationIssue],
    ) -> None:
        run_ids: set[str] = set()
        for index, record in enumerate(project.run_history):
            field = f"run_history[{index}]"
            if _is_blank(record.run_id):
                issues.append(ValidationIssue(f"{field}.run_id", "EMPTY_RUN_ID", "run_id 不能为空"))
            elif record.run_id in run_ids:
                issues.append(
                    ValidationIssue(f"{field}.run_id", "DUPLICATE_RUN_ID", "run_id 必须唯一")
                )
            else:
                run_ids.add(record.run_id)

            if record.case_id not in case_ids:
                issues.append(
                    ValidationIssue(
                        f"{field}.case_id", "UNKNOWN_RUN_CASE", "run record 引用了未知 case_id"
                    )
                )

    @staticmethod
    def _resolve_case_nodes(
        case_index: int,
        graph: SimulationGraph,
        asset_paths: dict[str, list[Path | None]],
        issues: list[ValidationIssue],
    ) -> tuple[Path, ...] | None:
        resolved_paths: list[Path] = []
        valid = True
        for node_index, node in enumerate(graph.nodes):
            field = f"simulation_cases[{case_index}].graph.nodes[{node_index}].model_path"
            matches = asset_paths.get(node.model_path, [])
            if not matches:
                issues.append(
                    ValidationIssue(
                        field,
                        "UNREGISTERED_MODEL_ASSET",
                        "node model_path 未注册为 ModelAsset.relative_path",
                    )
                )
                valid = False
                continue
            if len(matches) > 1:
                issues.append(
                    ValidationIssue(
                        field,
                        "AMBIGUOUS_MODEL_ASSET",
                        "node model_path 对应多个 ModelAsset",
                    )
                )
                valid = False
                continue
            resolved_path = matches[0]
            if resolved_path is None:
                issues.append(
                    ValidationIssue(
                        field,
                        "MODEL_ASSET_INVALID",
                        "node 对应的 ModelAsset 未通过完整性校验",
                    )
                )
                valid = False
                continue
            resolved_paths.append(resolved_path)
        return tuple(resolved_paths) if valid else None


def _resolved_graph(graph: SimulationGraph, resolved_paths: tuple[Path, ...]) -> SimulationGraph:
    return SimulationGraph(
        nodes=tuple(
            ModelNode(node.node_id, str(resolved_paths[index]), node.config)
            for index, node in enumerate(graph.nodes)
        ),
        connections=graph.connections,
    )


def _prefix_graph_issue(case_index: int, issue: ValidationIssue) -> ValidationIssue:
    base = f"simulation_cases[{case_index}]"
    if issue.field == "nodes" or issue.field.startswith(("nodes[", "connections", "connections[")):
        field = f"{base}.graph.{issue.field}"
    else:
        field = f"{base}.config.{issue.field}"
    return ValidationIssue(field, issue.code, issue.message)


def _prefix_issue(prefix: str, issue: ValidationIssue) -> ValidationIssue:
    return ValidationIssue(f"{prefix}.{issue.field}", issue.code, issue.message)


def _is_blank(value: object) -> bool:
    return not isinstance(value, str) or not value.strip()
