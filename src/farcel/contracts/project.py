"""Declarative, implementation-independent simulation project contracts."""

from __future__ import annotations

from dataclasses import dataclass

from farcel.contracts.graph import GraphSimulationConfig, SimulationGraph
from farcel.contracts.models import SimulationState


PROJECT_SCHEMA_VERSION = "1.0"


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
