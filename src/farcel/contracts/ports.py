from __future__ import annotations

from pathlib import Path
from typing import Protocol

from farcel.contracts.models import (
    ExportReport,
    ModelMetadata,
    ResultChunk,
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


class SimulationEngine(Protocol):
    """Stable interface consumed by CLI and GUI."""

    def load_fmu(self, path: str | Path) -> ModelMetadata: ...

    def validate_config(
        self, metadata: ModelMetadata, config: SimulationConfig
    ) -> ValidationReport: ...

    def create_session(
        self, model_id: str, config: SimulationConfig
    ) -> SessionHandle: ...

    def start(self, session: SessionHandle) -> None: ...

    def step(
        self, session: SessionHandle, step_size: float | None = None
    ) -> StepResult: ...

    def stop(self, session: SessionHandle) -> None: ...

    def get_state(self, session: SessionHandle) -> SimulationState: ...

    def get_results(self, session: SessionHandle) -> tuple[ResultChunk, ...]: ...

    def export_csv(self, session: SessionHandle, path: str | Path) -> ExportReport: ...

    def close_session(self, session: SessionHandle) -> None: ...
