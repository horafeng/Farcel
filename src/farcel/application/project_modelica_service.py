"""Application orchestration for registering one staged Modelica build."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from farcel.application.modelica_asset_staging import ModelicaAssetStagingService
from farcel.application.project_validation import ProjectValidator
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.modelica_provenance import (
    ModelicaAssetStagingRequest,
    ModelicaBuildProvenance,
    ProjectModelicaCompilation,
)
from farcel.contracts.project import LATEST_PROJECT_SCHEMA_VERSION, SimulationProject
from farcel.contracts.ports import ProjectRevisionRepository


class ProjectModelicaService:
    """Compile, stage, validate and conditionally register one Project asset."""

    def __init__(
        self,
        staging_service: ModelicaAssetStagingService,
        repository: ProjectRevisionRepository,
        validator: ProjectValidator,
    ) -> None:
        self._staging_service = staging_service
        self._repository = repository
        self._validator = validator

    def compile_and_register(
        self,
        project: SimulationProject,
        request: ModelicaAssetStagingRequest,
    ) -> ProjectModelicaCompilation:
        """Create one verified build package and register it in schema 1.1.

        A build package is intentionally retained when conditional persistence
        fails.  It is verified but unregistered, so the stable error details
        identify it for a future explicit recovery workflow without deleting
        files whose ownership may have changed.
        """

        root = _request_root(request)
        revision = self._repository.load_with_revision(root)
        _require_schema_1_1(project)
        if revision.project != project:
            raise EngineError(
                ErrorCode.PROJECT_CONFLICT,
                "调用者持有的 Project 已过期，拒绝覆盖最新 project.json。",
                {"stage": "pre_compile", "path": str(root / "project.json")},
            )
        _require_valid_project(self._validator, root, revision.project, "pre_compile")

        staged_build = self._staging_service.stage(request)
        updated = replace(
            revision.project,
            model_assets=revision.project.model_assets + (staged_build.model_asset,),
            modelica_build_provenance=(
                revision.project.modelica_build_provenance + (staged_build.provenance,)
            ),
        )
        _require_valid_project(self._validator, root, updated, "pre_persist")
        try:
            self._repository.save_if_unchanged(
                root,
                updated,
                revision.document_sha256,
            )
        except EngineError as exc:
            raise _registration_failure(exc, staged_build.provenance) from None
        return ProjectModelicaCompilation(updated, staged_build)

    @staticmethod
    def list_builds(project: SimulationProject) -> tuple[ModelicaBuildProvenance, ...]:
        """Return persisted provenance only; never compile or access the disk."""

        return project.modelica_build_provenance


def _request_root(request: ModelicaAssetStagingRequest) -> Path:
    try:
        return Path(request.project_root)
    except (TypeError, ValueError):
        raise EngineError(
            ErrorCode.CONFIG_ERROR,
            "project_root 必须是可解析的目录路径。",
            {"field": "project_root"},
        ) from None


def _require_schema_1_1(project: SimulationProject) -> None:
    if project.schema_version == "1.0":
        raise EngineError(
            ErrorCode.CONFIG_ERROR,
            "Project schema 1.0 必须先显式迁移到 1.1 才能登记 Modelica 构建。",
            {"field": "schema_version", "required_version": LATEST_PROJECT_SCHEMA_VERSION},
        )
    if project.schema_version != LATEST_PROJECT_SCHEMA_VERSION:
        raise EngineError(
            ErrorCode.CONFIG_ERROR,
            "当前 Project schema 不支持 Modelica 构建登记。",
            {"field": "schema_version", "actual_version": project.schema_version},
        )


def _require_valid_project(
    validator: ProjectValidator,
    root: Path,
    project: SimulationProject,
    stage: str,
) -> None:
    report = validator.validate(root, project)
    if report.is_valid:
        return
    raise EngineError(
        ErrorCode.CONFIG_ERROR,
        "Project 验证失败，拒绝执行 Modelica 构建登记。",
        {
            "stage": stage,
            "issues": tuple(
                {"field": issue.field, "code": issue.code, "message": issue.message}
                for issue in report.issues
            ),
        },
    )


def _registration_failure(
    failure: EngineError,
    provenance: ModelicaBuildProvenance,
) -> EngineError:
    details = dict(failure.details)
    details.update(
        {
            "stage": "project_persistence",
            "unregistered_build": {
                "build_id": provenance.build_id,
                "fmu_relative_path": (
                    f"modelica-builds/build-{provenance.build_id}/model/model.fmu"
                ),
            },
        }
    )
    return EngineError(
        failure.code,
        "Modelica 构建包已验证，但 project.json 登记未完成。",
        details,
    )
