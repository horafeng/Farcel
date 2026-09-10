"""Thin serial orchestration over the existing persistent project-case API."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.graph import GraphSimulationResult
from farcel.contracts.models import RunProgress, SimulationState, ValidationIssue
from farcel.contracts.project import ProjectRunRecord, SimulationProject
from farcel.contracts.project_batch import (
    ProjectBatchProgress,
    ProjectBatchRunItem,
    ProjectBatchRunResult,
)
from farcel.contracts.run_control import RunControl


class ProjectCaseRunCallable(Protocol):
    """The existing one-case public workflow used by the batch service."""

    def __call__(
        self,
        project_root: str | Path,
        project: SimulationProject,
        case_id: str,
        *,
        control: RunControl | None = None,
        on_progress: Callable[[RunProgress], None] | None = None,
    ) -> tuple[SimulationProject, ProjectRunRecord, GraphSimulationResult]: ...


class ProjectBatchService:
    """Run requested cases serially through one persistent case entry point."""

    def __init__(self, run_project_case: ProjectCaseRunCallable) -> None:
        self._run_project_case = run_project_case

    def run(
        self,
        project_root: str | Path,
        project: SimulationProject,
        case_ids: tuple[str, ...],
        *,
        control: RunControl | None = None,
        on_progress: Callable[[ProjectBatchProgress], None] | None = None,
    ) -> ProjectBatchRunResult:
        issues = _preflight_issues(project, case_ids)
        if issues:
            raise EngineError(
                ErrorCode.CONFIG_ERROR,
                "项目批量运行配置无效",
                {"issues": tuple(_issue_details(issue) for issue in issues)},
            )

        if control is not None and control.stop_requested:
            raise EngineError(ErrorCode.CANCELLED, "项目批量运行开始前已请求停止")

        current_project = project
        items: list[ProjectBatchRunItem] = []
        case_count = len(case_ids)
        for case_number, case_id in enumerate(case_ids, start=1):
            if control is not None and control.stop_requested:
                if not items:
                    raise EngineError(ErrorCode.CANCELLED, "项目批量运行开始前已请求停止")
                return _batch_result(current_project, case_ids, items, stopped=True)

            callback = _progress_callback(
                on_progress, case_id, case_number, case_count
            )
            try:
                updated_project, record, result = self._run_project_case(
                    project_root,
                    current_project,
                    case_id,
                    control=control,
                    on_progress=callback,
                )
            except EngineError as error:
                raise _with_batch_context(
                    error, case_id, case_number, case_count, items
                ) from None

            items.append(ProjectBatchRunItem(case_id, record, result))
            current_project = updated_project
            if result.completion_state is SimulationState.STOPPED:
                return _batch_result(current_project, case_ids, items, stopped=True)
            if control is not None and control.stop_requested:
                return _batch_result(current_project, case_ids, items, stopped=True)

        return _batch_result(current_project, case_ids, items, stopped=False)


def _preflight_issues(
    project: SimulationProject, case_ids: tuple[str, ...]
) -> tuple[ValidationIssue, ...]:
    if not case_ids:
        return (ValidationIssue("case_ids", "EMPTY_CASE_IDS", "case_ids 不能为空"),)

    known_case_ids = {case.case_id for case in project.simulation_cases}
    seen_case_ids: set[str] = set()
    issues: list[ValidationIssue] = []
    for index, case_id in enumerate(case_ids):
        field = f"case_ids[{index}]"
        if case_id in seen_case_ids:
            issues.append(
                ValidationIssue(
                    field,
                    "DUPLICATE_REQUESTED_CASE_ID",
                    "case_id 在批量请求中重复",
                )
            )
        else:
            seen_case_ids.add(case_id)
        if case_id not in known_case_ids:
            issues.append(
                ValidationIssue(field, "UNKNOWN_CASE_ID", "case_id 不存在")
            )
    return tuple(issues)


def _progress_callback(
    callback: Callable[[ProjectBatchProgress], None] | None,
    case_id: str,
    case_number: int,
    case_count: int,
) -> Callable[[RunProgress], None] | None:
    if callback is None:
        return None

    def forward(progress: RunProgress) -> None:
        callback(ProjectBatchProgress(case_id, case_number, case_count, progress))

    return forward


def _with_batch_context(
    error: EngineError,
    case_id: str,
    case_number: int,
    case_count: int,
    items: list[ProjectBatchRunItem],
) -> EngineError:
    details = dict(error.details)
    details.update(
        {
            "batch_failed_case_id": case_id,
            "batch_failed_case_number": case_number,
            "batch_case_count": case_count,
            "batch_completed_case_ids": tuple(item.case_id for item in items),
            "batch_completed_run_ids": tuple(item.record.run_id for item in items),
        }
    )
    return EngineError(error.code, error.message, details)


def _batch_result(
    project: SimulationProject,
    requested_case_ids: tuple[str, ...],
    items: list[ProjectBatchRunItem],
    *,
    stopped: bool,
) -> ProjectBatchRunResult:
    return ProjectBatchRunResult(project, requested_case_ids, tuple(items), stopped)


def _issue_details(issue: ValidationIssue) -> dict[str, str]:
    return {"field": issue.field, "code": issue.code, "message": issue.message}
