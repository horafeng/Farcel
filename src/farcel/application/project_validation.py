"""Project-level validation that reuses asset and graph validation boundaries."""

from __future__ import annotations

from pathlib import Path
import hashlib
import re

from farcel.application.graph_validation import GraphValidator
from farcel.application.project_assets import ProjectAssetValidator
from farcel.application.modelica_provenance_validation import (
    validate_modelica_build_provenance,
)
from farcel.contracts.graph import ModelNode, SimulationGraph
from farcel.contracts.models import ValidationIssue, ValidationReport
from farcel.contracts.ports import ModelImporter
from farcel.contracts.project import (
    PROJECT_SCHEMA_VERSION,
    SUPPORTED_PROJECT_SCHEMA_VERSIONS,
    ModelAsset,
    SimulationProject,
)
from farcel.contracts.project_paths import project_relative_path_issue
from farcel.contracts.modelica_provenance import ModelicaBuildProvenance


_BUILD_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_HASH_CHUNK_SIZE = 64 * 1024


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
        self._validate_modelica_provenance(project_root, project, issues)
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
        if project.schema_version not in SUPPORTED_PROJECT_SCHEMA_VERSIONS:
            issues.append(
                ValidationIssue(
                    "schema_version",
                    "UNSUPPORTED_PROJECT_SCHEMA",
                    "仅支持 project schema 1.0 或 1.1",
                )
            )
        elif project.schema_version == PROJECT_SCHEMA_VERSION and project.modelica_build_provenance:
            issues.append(
                ValidationIssue(
                    "modelica_build_provenance",
                    "PROVENANCE_NOT_ALLOWED_IN_SCHEMA_1_0",
                    "schema 1.0 不允许包含 Modelica provenance",
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

    def _validate_modelica_provenance(
        self,
        project_root: Path,
        project: SimulationProject,
        issues: list[ValidationIssue],
    ) -> None:
        if project.schema_version != "1.1":
            return
        build_ids: set[str] = set()
        asset_provenance: set[str] = set()
        assets_by_id = {asset.asset_id: asset for asset in project.model_assets}
        for index, provenance in enumerate(project.modelica_build_provenance):
            field = f"modelica_build_provenance[{index}]"
            if not isinstance(provenance, ModelicaBuildProvenance):
                issues.append(
                    ValidationIssue(
                        field,
                        "INVALID_MODELICA_PROVENANCE",
                        "modelica_build_provenance 必须是 ModelicaBuildProvenance",
                    )
                )
                continue
            report = validate_modelica_build_provenance(
                provenance,
                model_assets=project.model_assets,
            )
            issues.extend(_prefix_issue(field, issue) for issue in report.issues)
            build_id = provenance.build_id
            if build_id in build_ids:
                issues.append(
                    ValidationIssue(f"{field}.build_id", "DUPLICATE_BUILD_ID", "build_id 必须唯一")
                )
            else:
                build_ids.add(build_id)
            if _BUILD_ID_PATTERN.fullmatch(build_id or "") is None:
                issues.append(
                    ValidationIssue(
                        f"{field}.build_id",
                        "INVALID_BUILD_ID",
                        "build_id 必须是 32 位小写十六进制标识",
                    )
                )
            if provenance.fmu_asset_id in asset_provenance:
                issues.append(
                    ValidationIssue(
                        f"{field}.fmu_asset_id",
                        "DUPLICATE_FMU_ASSET_PROVENANCE",
                        "同一 ModelAsset 只能关联一条 Modelica provenance",
                    )
                )
            else:
                asset_provenance.add(provenance.fmu_asset_id)
            asset = assets_by_id.get(provenance.fmu_asset_id)
            self._validate_provenance_files(project_root, provenance, asset, field, issues)

    @staticmethod
    def _validate_provenance_files(
        project_root: Path,
        provenance: object,
        asset: ModelAsset | None,
        field: str,
        issues: list[ValidationIssue],
    ) -> None:
        source_snapshot = getattr(provenance, "source_snapshot", None)
        source_relative_path = getattr(source_snapshot, "relative_path", None)
        source_sha256 = getattr(source_snapshot, "sha256", None)
        path_issue = project_relative_path_issue(source_relative_path)
        if path_issue is not None:
            issues.append(
                ValidationIssue(
                    f"{field}.source_snapshot.relative_path",
                    path_issue.code,
                    path_issue.message,
                )
            )
            return
        try:
            root = Path(project_root).resolve(strict=True)
            source = root / source_relative_path
            if source.is_symlink():
                raise OSError("source snapshot 是符号链接")
            source = source.resolve(strict=True)
            source.relative_to(root)
            if not source.is_file():
                raise OSError("source snapshot 不是普通文件")
            actual_sha256 = _sha256(source)
        except (OSError, RuntimeError, ValueError) as exc:
            issues.append(
                ValidationIssue(
                    f"{field}.source_snapshot.relative_path",
                    "SOURCE_SNAPSHOT_MISSING_OR_UNSAFE",
                    f"无法安全访问源快照: {exc}",
                )
            )
            return
        if actual_sha256 != source_sha256:
            issues.append(
                ValidationIssue(
                    f"{field}.source_snapshot.sha256",
                    "SOURCE_SNAPSHOT_CHECKSUM_MISMATCH",
                    "源快照 SHA-256 不匹配",
                )
            )
        build_prefix = f"modelica-builds/build-{getattr(provenance, 'build_id', '')}/"
        if (
            source_relative_path != f"{build_prefix}source/model.mo"
            or asset is None
            or asset.relative_path != f"{build_prefix}model/model.fmu"
        ):
            issues.append(
                ValidationIssue(
                    field,
                    "BUILD_PACKAGE_ASSOCIATION_INVALID",
                    "Modelica provenance 必须关联同一 build 目录中的源快照和 FMU 资产",
                )
            )

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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()
