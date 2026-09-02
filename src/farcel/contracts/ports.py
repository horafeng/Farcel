from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from typing import Any, Mapping, Protocol

from farcel.contracts.models import (
    DiscreteStateUpdate,
    ExportReport,
    IntegratorStepResult,
    ModelExchangeInitialization,
    ModelMetadata,
    ResultChunk,
    SessionHandle,
    SimulationConfig,
    SimulationResult,
    SimulationState,
    SolverAdvanceResult,
    SolverOptions,
    SolverResetReason,
    RunProgress,
    StepResult,
    ValidationReport,
)
from farcel.contracts.run_control import RunControl


class ModelImporter(Protocol):
    """Boundary implemented by an FMU technology adapter."""

    def load(self, path: Path) -> ModelMetadata:
        """Parse and normalise an FMU without exposing adapter-native types."""


class SimulationSession(Protocol):
    """Implementation-independent lifecycle of one instantiated FMU."""

    def initialize(self) -> None: ...

    def set_inputs(self, values: Mapping[str, Any]) -> None: ...

    def step(self, current_time: float, step_size: float) -> StepResult: ...

    def read_outputs(self) -> Mapping[str, Any]: ...

    def terminate(self) -> None: ...

    def close(self) -> None: ...


class SessionFactory(Protocol):
    """Create a concrete session behind the Farcel session boundary."""

    def create(
        self, metadata: ModelMetadata, config: SimulationConfig
    ) -> SimulationSession: ...


class ModelExchangeSession(Protocol):
    """Implementation-independent FMI Model Exchange lifecycle."""

    def initialize(self) -> ModelExchangeInitialization: ...
    def set_inputs(self, values: Mapping[str, Any]) -> None: ...
    def set_time(self, time: float) -> None: ...
    def get_continuous_states(self) -> tuple[float, ...]: ...
    def set_continuous_states(self, states: tuple[float, ...]) -> None: ...
    def get_derivatives(self) -> tuple[float, ...]: ...
    def get_event_indicators(self) -> tuple[float, ...]: ...
    def completed_integrator_step(self) -> IntegratorStepResult: ...
    def enter_event_mode(self) -> None: ...
    def update_discrete_states(self) -> DiscreteStateUpdate: ...
    def enter_continuous_time_mode(self) -> None: ...
    def read_outputs(self) -> Mapping[str, Any]: ...
    def terminate(self) -> None: ...
    def close(self) -> None: ...


class ModelExchangeSessionFactory(Protocol):
    """Create a Model Exchange session without adapter-native objects."""

    def create(
        self, metadata: ModelMetadata, config: SimulationConfig
    ) -> ModelExchangeSession: ...


class ModelExchangeProblem(Protocol):
    """Farcel-owned callbacks used by a numerical Model Exchange solver."""

    def get_initial_states(self) -> tuple[float, ...]: ...
    def set_state(self, time: float, states: tuple[float, ...]) -> None: ...
    def get_derivatives(self) -> tuple[float, ...]: ...
    def get_event_indicators(self) -> tuple[float, ...]: ...


class SolverAdapter(Protocol):
    """Advance a Farcel-owned Model Exchange problem to a target time."""

    def initialize(self, problem: ModelExchangeProblem, options: SolverOptions) -> None: ...
    def integrate_to(self, target_time: float) -> SolverAdvanceResult: ...
    def reset(self, time: float, reason: SolverResetReason) -> None: ...
    def close(self) -> None: ...


class SolverFactory(Protocol):
    """Create a solver adapter selected by a future Model Exchange runner."""

    def create(self) -> SolverAdapter: ...


class ResultExporter(Protocol):
    """Persist an existing canonical result without rerunning a simulation."""

    def export(
        self, result: SimulationResult, destination: Path
    ) -> ExportReport: ...


class SimulationEngine(Protocol):
    """Stable interface consumed by CLI and GUI."""

    def load_fmu(self, path: str | Path) -> ModelMetadata: ...

    def validate_config(
        self, metadata: ModelMetadata, config: SimulationConfig
    ) -> ValidationReport: ...

    def create_session(
        self, model_id: str, config: SimulationConfig
    ) -> SessionHandle: ...

    def initialize(self, session: SessionHandle) -> None: ...

    def step(
        self, session: SessionHandle, step_size: float | None = None
    ) -> StepResult: ...

    def read_outputs(self, session: SessionHandle) -> Mapping[str, Any]: ...

    def terminate(self, session: SessionHandle) -> None: ...

    def get_state(self, session: SessionHandle) -> SimulationState: ...

    def close_session(self, session: SessionHandle) -> None: ...

    def run_fmu(
        self,
        path: str | Path,
        config: SimulationConfig,
        *,
        control: RunControl | None = None,
        on_progress: Callable[[RunProgress], None] | None = None,
        on_result_chunk: Callable[[ResultChunk], None] | None = None,
        result_chunk_size: int = 256,
    ) -> SimulationResult: ...

    def export_result(
        self, result: SimulationResult, destination: str | Path
    ) -> ExportReport: ...
