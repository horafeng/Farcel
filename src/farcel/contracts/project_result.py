"""Immutable, portable provenance and result contracts for one project run."""

from __future__ import annotations

from dataclasses import dataclass

from farcel.contracts.graph import GraphSimulationResult
from farcel.contracts.project import SimulationCase


PROJECT_RUN_ARTIFACT_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class ProjectAssetSnapshot:
    asset_id: str
    relative_path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ProjectRunArtifact:
    run_id: str
    project_id: str
    case_snapshot: SimulationCase
    asset_snapshots: tuple[ProjectAssetSnapshot, ...]
    result: GraphSimulationResult
    schema_version: str = PROJECT_RUN_ARTIFACT_SCHEMA_VERSION
