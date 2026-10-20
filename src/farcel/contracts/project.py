"""Declarative, implementation-independent simulation project contracts."""

from __future__ import annotations

from dataclasses import dataclass

from farcel.contracts.graph import GraphSimulationConfig, SimulationGraph
from farcel.contracts.modelica_provenance import ModelicaBuildProvenance
from farcel.contracts.models import SimulationState


PROJECT_SCHEMA_VERSION = "1.0"
LATEST_PROJECT_SCHEMA_VERSION = "1.1"
SUPPORTED_PROJECT_SCHEMA_VERSIONS = frozenset(
    (PROJECT_SCHEMA_VERSION, LATEST_PROJECT_SCHEMA_VERSION)
)

@dataclass(frozen=True, slots=True)
class ModelAsset:
    asset_id: str
    display_name: str
    relative_path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class SimulationCase:
    case_id: str
    name: str
    graph: SimulationGraph
    config: GraphSimulationConfig


@dataclass(frozen=True, slots=True)
class ProjectRunRecord:
    run_id: str
    case_id: str
    completion_state: SimulationState
    final_time: float
    completed_steps: int
    result_path: str


@dataclass(frozen=True, slots=True)
class SimulationProject:
    project_id: str
    name: str
    model_assets: tuple[ModelAsset, ...] = ()
    simulation_cases: tuple[SimulationCase, ...] = ()
    run_history: tuple[ProjectRunRecord, ...] = ()
    schema_version: str = PROJECT_SCHEMA_VERSION
    modelica_build_provenance: tuple[ModelicaBuildProvenance, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectSchemaMigrationCandidate:
    """One repository-read 1.0 document guarded by its original digest."""

    project: SimulationProject
    document_sha256: str


@dataclass(frozen=True, slots=True)
class ProjectSchemaMigrationResult:
    """Successful explicit schema migration and its recoverable backup."""

    project: SimulationProject
    backup_relative_path: str
