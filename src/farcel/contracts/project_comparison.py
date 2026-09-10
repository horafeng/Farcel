"""Immutable, artifact-only contracts for historical project-run comparison."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from farcel.contracts.project_result import ProjectRunArtifact


class SignalStatisticsStatus(str, Enum):
    AVAILABLE = "available"
    MISSING_SIGNAL = "missing_signal"
    ARRAY_SIGNAL = "array_signal"
    NON_NUMERIC_SIGNAL = "non_numeric_signal"
    EMPTY_SIGNAL = "empty_signal"


@dataclass(frozen=True, slots=True)
class SignalStatistics:
    status: SignalStatisticsStatus
    minimum: float | None = None
    maximum: float | None = None
    mean: float | None = None
    final: float | None = None

    def __post_init__(self) -> None:
        values = (self.minimum, self.maximum, self.mean, self.final)
        if self.status is not SignalStatisticsStatus.AVAILABLE and any(
            value is not None for value in values
        ):
            raise ValueError("不可用的 SignalStatistics 不能包含数值")

    @property
    def available(self) -> bool:
        return self.status is SignalStatisticsStatus.AVAILABLE


@dataclass(frozen=True, slots=True)
class ProjectRunSignalSeries:
    run_id: str
    timestamps: tuple[float, ...]
    samples: tuple[Any, ...]
    statistics: SignalStatistics


@dataclass(frozen=True, slots=True)
class ProjectRunSignalComparison:
    node_id: str
    variable_name: str
    series: tuple[ProjectRunSignalSeries, ...]


@dataclass(frozen=True, slots=True)
class ProjectRunComparison:
    sources: tuple[ProjectRunArtifact, ...]
    signals: tuple[ProjectRunSignalComparison, ...]
