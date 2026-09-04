from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from farcel.contracts.models import InputUpdate, InterfaceType, SimulationState


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


@dataclass(frozen=True, slots=True)
class GraphSimulationResult:
    start_time: float
    stop_time: float
    step_size: float
    completed_steps: int
    final_time: float
    completion_state: SimulationState
    timestamps: tuple[float, ...]
    node_outputs: Mapping[str, Mapping[str, tuple[Any, ...]]]

    def __post_init__(self) -> None:
        if not self.timestamps or not all(math.isfinite(time) for time in self.timestamps):
            raise ValueError("GraphSimulationResult 必须包含有限时间样本")
        if any(left >= right for left, right in zip(self.timestamps, self.timestamps[1:])):
            raise ValueError("GraphSimulationResult 时间轴必须严格单调递增")
        if self.timestamps[0] != self.start_time or self.timestamps[-1] != self.final_time:
            raise ValueError("GraphSimulationResult 时间轴端点不匹配")
        if self.completed_steps < 0:
            raise ValueError("completed_steps 不能为负数")
        if not self.start_time <= self.final_time <= self.stop_time:
            raise ValueError("final_time 必须位于 start_time 与 stop_time 之间")
        if any(len(samples) != len(self.timestamps) for outputs in self.node_outputs.values() for samples in outputs.values()):
            raise ValueError("GraphSimulationResult 输出样本数必须匹配时间轴")

    @property
    def sample_count(self) -> int:
        return len(self.timestamps)

    @property
    def successful(self) -> bool:
        return self.completion_state is SimulationState.COMPLETED
