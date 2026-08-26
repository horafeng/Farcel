"""Public, implementation-independent engine contracts."""

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import (
    CapabilitySet,
    DefaultExperiment,
    ExportReport,
    InterfaceCapability,
    InterfaceType,
    ModelMetadata,
    ResultChunk,
    RunSummary,
    SessionHandle,
    SimulationConfig,
    SimulationState,
    StepResult,
    StepStatus,
    ValidationIssue,
    ValidationReport,
    VariableMetadata,
)
from farcel.contracts.ports import (
    ModelImporter,
    SessionFactory,
    SimulationEngine,
    SimulationSession,
)

__all__ = [
    "CapabilitySet",
    "DefaultExperiment",
    "EngineError",
    "ErrorCode",
    "ExportReport",
    "InterfaceType",
    "InterfaceCapability",
    "ModelImporter",
    "ModelMetadata",
    "ResultChunk",
    "RunSummary",
    "SessionFactory",
    "SessionHandle",
    "SimulationConfig",
    "SimulationEngine",
    "SimulationSession",
    "SimulationState",
    "StepResult",
    "StepStatus",
    "ValidationIssue",
    "ValidationReport",
    "VariableMetadata",
]
