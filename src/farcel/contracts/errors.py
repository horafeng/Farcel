from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class ErrorCode(str, Enum):
    IMPORT_ERROR = "IMPORT_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNSUPPORTED_FMI = "UNSUPPORTED_FMI"
    UNSUPPORTED_INTERFACE = "UNSUPPORTED_INTERFACE"
    PLATFORM_BINARY_MISSING = "PLATFORM_BINARY_MISSING"
    CONFIG_ERROR = "CONFIG_ERROR"
    INSTANTIATION_ERROR = "INSTANTIATION_ERROR"
    INITIALIZATION_ERROR = "INITIALIZATION_ERROR"
    PARAMETER_SET_ERROR = "PARAMETER_SET_ERROR"
    STEP_ERROR = "STEP_ERROR"
    TERMINATION_ERROR = "TERMINATION_ERROR"
    CLEANUP_ERROR = "CLEANUP_ERROR"
    FMI_RUNTIME_ERROR = "FMI_RUNTIME_ERROR"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    EXPORT_ERROR = "EXPORT_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


@dataclass(slots=True)
class EngineError(Exception):
    """Stable error exposed to GUI and CLI consumers."""

    code: ErrorCode
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.code.value}: {self.message}"
