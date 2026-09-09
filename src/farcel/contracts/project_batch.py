"""Farcel-owned contracts for serial SimulationProject batch execution."""

from __future__ import annotations

from dataclasses import dataclass

from farcel.contracts.graph import GraphSimulationResult
from farcel.contracts.models import RunProgress
from farcel.contracts.project import ProjectRunRecord, SimulationProject


@dataclass(frozen=True, slots=True)
class ProjectBatchRunItem:
    """One case result already persisted by the existing project workflow."""

    case_id: str
    record: ProjectRunRecord
    result: GraphSimulationResult


@dataclass(frozen=True, slots=True)
class ProjectBatchRunResult:
    """The committed portion of one caller-ordered serial batch."""

    updated_project: SimulationProject
    requested_case_ids: tuple[str, ...]
    items: tuple[ProjectBatchRunItem, ...]
    stopped: bool = False

    @property
    def completed_count(self) -> int:
        return len(self.items)


@dataclass(frozen=True, slots=True)
class ProjectBatchProgress:
    """Existing run progress annotated with its serial batch position."""

    case_id: str
    case_number: int
    case_count: int
    run_progress: RunProgress
