from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from farcel.contracts.models import InputUpdate, InterfaceType


@dataclass(frozen=True, slots=True)
class PortReference:
    """A declarative graph endpoint resolved from model metadata in Phase 4.1."""

    node_id: str
    variable_name: str


@dataclass(frozen=True, slots=True)
class Connection:
    """A declarative source-to-target graph connection."""

    source: PortReference
    target: PortReference


@dataclass(frozen=True, slots=True)
class ModelNodeConfig:
    """Node-local configuration without graph-global time settings.

    ``selected_outputs`` chooses values to record in a future graph result. It
    does not limit connection dependencies: a runtime must still read an output
    needed by a ``Connection`` even when it is not selected for recording.
    """

    parameters: Mapping[str, Any] = field(default_factory=dict)
    initial_inputs: Mapping[str, Any] = field(default_factory=dict)
    input_schedule: tuple[InputUpdate, ...] = ()
    selected_outputs: tuple[str, ...] = ()
    relative_tolerance: float | None = None
    execution_interface: InterfaceType | None = None


@dataclass(frozen=True, slots=True)
class ModelNode:
    """A declarative model path and its node-local configuration."""

    node_id: str
    model_path: str
    config: ModelNodeConfig = field(default_factory=ModelNodeConfig)


@dataclass(frozen=True, slots=True)
class SimulationGraph:
    """A declarative graph; semantic validation belongs to Phase 4.1."""

    nodes: tuple[ModelNode, ...] = ()
    connections: tuple[Connection, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphSimulationConfig:
    """Graph-global timing and sampling configuration without validation."""

    schema_version: str = "1.0"
    start_time: float = 0.0
    stop_time: float = 1.0
    communication_step: float = 0.01
    output_interval: float | None = None
