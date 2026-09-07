"""Persist one completed project case result without running a simulation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from farcel.application.project_results import build_project_run_artifact
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.graph import GraphSimulationResult
from farcel.contracts.models import ValidationIssue
from farcel.contracts.ports import ProjectRepository, ProjectRunArtifactRepository
from farcel.contracts.project import ProjectRunRecord, SimulationProject


class ProjectRunPersistenceService:
    """Coordinate existing artifact and project repositories for one run."""

    def __init__(
        self,
        project_repository: ProjectRepository,
        artifact_repository: ProjectRunArtifactRepository,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._project_repository = project_repository
        self._artifact_repository = artifact_repository
        self._run_id_factory = run_id_factory or _generate_run_id

    def persist_run(
        self,
        project_root: Path,
        project: SimulationProject,
        case_id: str,
        result: GraphSimulationResult,
    ) -> tuple[SimulationProject, ProjectRunRecord]:
        run_id = self._run_id_factory()
        if any(record.run_id == run_id for record in project.run_history):
            raise _duplicate_run_id_error()

        artifact = build_project_run_artifact(project, case_id, run_id, result)
        result_path = self._artifact_repository.save(project_root, artifact)
        record = ProjectRunRecord(
            run_id=run_id,
            case_id=case_id,
            completion_state=result.completion_state,
            final_time=result.final_time,
            completed_steps=result.completed_steps,
            result_path=result_path,
        )
        updated_project = replace(
            project,
            run_history=project.run_history + (record,),
        )
        try:
            self._project_repository.save(project_root, updated_project)
        except EngineError as error:
            details = dict(error.details)
            details.update(
                {
                    "run_id": run_id,
                    "orphan_result_path": result_path,
                    "history_committed": False,
                }
            )
            raise EngineError(
                error.code,
                "result artifact 已保存，但 project.json 更新失败",
                details,
            ) from None
        return updated_project, record


def _generate_run_id() -> str:
    return uuid4().hex


def _duplicate_run_id_error() -> EngineError:
    issue = ValidationIssue("run_id", "DUPLICATE_RUN_ID", "run_id 已存在于 run_history")
    return EngineError(
        ErrorCode.CONFIG_ERROR,
        "Project run persistence 配置无效",
        {"issues": ({"field": issue.field, "code": issue.code, "message": issue.message},)},
    )
