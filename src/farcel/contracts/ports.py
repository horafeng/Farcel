from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from typing import Any, Mapping, Protocol

from farcel.contracts.models import (
    ExportReport,
    ModelMetadata,
    SessionHandle,
    SimulationConfig,
    SimulationResult,
    SimulationState,
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
    ) -> SimulationResult: ...

    def export_result(
        self, result: SimulationResult, destination: str | Path
    ) -> ExportReport: ...
