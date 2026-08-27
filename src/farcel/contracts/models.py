from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class InterfaceType(str, Enum):
    CO_SIMULATION = "co_simulation"
    MODEL_EXCHANGE = "model_exchange"
    SCHEDULED_EXECUTION = "scheduled_execution"


@dataclass(frozen=True, slots=True)
class DefaultExperiment:
    start_time: float | None = None
    stop_time: float | None = None
    tolerance: float | None = None
    step_size: float | None = None


@dataclass(frozen=True, slots=True)
class CapabilitySet:
    can_execute: bool = False
    supports_event_mode: bool = False
    supports_early_return: bool = False


@dataclass(frozen=True, slots=True)
class InterfaceCapability:
    interface_type: InterfaceType
    model_identifier: str | None = None
    can_execute: bool = False
    needs_execution_tool: bool = False
    can_handle_variable_step: bool = False
    supports_event_mode: bool = False
    supports_early_return: bool = False


@dataclass(frozen=True, slots=True)
class VariableMetadata:
    name: str
    value_reference: int
    data_type: str
    causality: str | None = None
    variability: str | None = None
    initial: str | None = None
    start: Any = None
    minimum: Any = None
    maximum: Any = None
    unit: str | None = None
    display_unit: str | None = None
    description: str | None = None
    declared_type: str | None = None
    shape: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelMetadata:
    model_id: str
    source_path: str
    fmi_version: str
    model_name: str
    interface_types: tuple[InterfaceType, ...]
    instantiation_token: str | None = None
    executable_interface: InterfaceType | None = None
    description: str | None = None
    generation_tool: str | None = None
    generation_time: str | None = None
    platforms: tuple[str, ...] = ()
    interface_capabilities: tuple[InterfaceCapability, ...] = ()
    default_experiment: DefaultExperiment = field(default_factory=DefaultExperiment)
    capabilities: CapabilitySet = field(default_factory=CapabilitySet)
    variables: tuple[VariableMetadata, ...] = ()
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    schema_version: str = "1.0"
    start_time: float = 0.0
    stop_time: float = 1.0
    communication_step: float = 0.01
    output_interval: float = 0.01
    relative_tolerance: float | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    initial_inputs: Mapping[str, Any] = field(default_factory=dict)
    selected_outputs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    field: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.issues


class SimulationState(str, Enum):
    CREATED = "created"
    READY = "ready"
    RUNNING = "running"
    STOPPING = "stopping"
    COMPLETED = "completed"
    STOPPED = "stopped"
    ERROR = "error"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class SessionHandle:
    session_id: str


class StepStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class StepResult:
    requested_time: float
    reached_time: float
    step_size: float
    status: StepStatus = StepStatus.SUCCESS
    event_encountered: bool = False
    early_return: bool = False
    terminate_requested: bool = False


@dataclass(frozen=True, slots=True)
class ResultChunk:
    run_id: str
    sequence: int
    time: tuple[float, ...]
    columns: Mapping[str, tuple[Any, ...]]
    final_chunk: bool = False


@dataclass(frozen=True, slots=True)
class ExportReport:
    destination: str
    row_count: int


@dataclass(frozen=True, slots=True)
class RunSummary:
    fmu_path: str
    start_time: float
    stop_time: float
    step_size: float
    completed_steps: int
    final_time: float
    successful: bool


@dataclass(frozen=True, slots=True)
class SimulationResult:
    fmu_path: str
    start_time: float
    stop_time: float
    step_size: float
    completed_steps: int
    final_time: float
    completion_state: SimulationState
    timestamps: tuple[float, ...]
    outputs: Mapping[str, tuple[Any, ...]]

    def __post_init__(self) -> None:
        if not self.timestamps:
            raise ValueError("SimulationResult 必须包含初始时间样本")
        if len(self.timestamps) != self.completed_steps + 1:
            raise ValueError("时间样本数量必须等于 completed_steps + 1")
        if not all(math.isfinite(timestamp) for timestamp in self.timestamps):
            raise ValueError("时间轴只能包含有限数值")
        if any(
            left >= right
            for left, right in zip(self.timestamps, self.timestamps[1:])
        ):
            raise ValueError("时间轴必须严格单调递增")
        if self.timestamps[0] != self.start_time:
            raise ValueError("首个时间样本必须等于 start_time")
        if self.timestamps[-1] != self.final_time:
            raise ValueError("最后时间样本必须等于 final_time")
        if any(len(values) != len(self.timestamps) for values in self.outputs.values()):
            raise ValueError("每个输出变量的样本数量必须与时间轴一致")

    @property
    def sample_count(self) -> int:
        return len(self.timestamps)

    @property
    def successful(self) -> bool:
        return self.completion_state is SimulationState.COMPLETED
