from __future__ import annotations

from pathlib import Path
from typing import Protocol

from farcel.contracts.models import (
    ModelMetadata,
    RunSummary,
    SessionHandle,
    SimulationConfig,
    SimulationState,
    StepResult,
    ValidationReport,
)


class ModelImporter(Protocol):
    """Boundary implemented by an FMU technology adapter."""

    def load(self, path: Path) -> ModelMetadata:
        """Parse and normalise an FMU without exposing adapter-native types."""


class SimulationSession(Protocol):
    """Implementation-independent lifecycle of one instantiated FMU."""

    def initialize(self) -> None: ...

    def step(self, current_time: float, step_size: float) -> StepResult: ...

    def terminate(self) -> None: ...

    def close(self) -> None: ...


class SessionFactory(Protocol):
    """Create a concrete session behind the Farcel session boundary."""

    def create(
        self, metadata: ModelMetadata, config: SimulationConfig
    ) -> SimulationSession: ...


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

    def terminate(self, session: SessionHandle) -> None: ...

    def get_state(self, session: SessionHandle) -> SimulationState: ...

    def close_session(self, session: SessionHandle) -> None: ...

    def run_fmu(self, path: str | Path, config: SimulationConfig) -> RunSummary: ...
