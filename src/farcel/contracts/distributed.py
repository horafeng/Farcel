"""Farcel-owned deployment declarations for future distributed execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PlacementKind(str, Enum):
    LOCAL = "local"
    WORKER = "worker"


@dataclass(frozen=True, slots=True)
class WorkerEndpoint:
    host: str
    port: int


@dataclass(frozen=True, slots=True)
class WorkerDescriptor:
    worker_id: str
    endpoint: WorkerEndpoint


@dataclass(frozen=True, slots=True)
class NodePlacement:
    node_id: str
    kind: PlacementKind = PlacementKind.LOCAL
    worker_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    workers: tuple[WorkerDescriptor, ...] = ()
    placements: tuple[NodePlacement, ...] = ()
