"""Modelica compiler environment contracts owned by Farcel."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ModelicaEnvironmentStatus(str, Enum):
    """当前主机上 Modelica 编译器环境的可用状态。"""

    AVAILABLE = "available"
    NOT_FOUND = "not_found"
    UNUSABLE = "unusable"


@dataclass(frozen=True, slots=True)
class ModelicaEnvironmentInfo:
    """供 GUI、CLI 和 Python 调用方消费的编译器环境摘要。"""

    status: ModelicaEnvironmentStatus
    compiler_name: str
    compiler_version: str | None
    diagnostic_message: str

    @property
    def is_available(self) -> bool:
        """编译器是否已经过实际启动与版本输出验证。"""

        return self.status is ModelicaEnvironmentStatus.AVAILABLE


class ModelicaDiagnosticSeverity(str, Enum):
    """Modelica 编译诊断的稳定严重级别。"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ModelicaDiagnostic:
    """不暴露编译器原始输出的 Farcel-owned 编译诊断。"""

    severity: ModelicaDiagnosticSeverity
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ModelicaCompileRequest:
    """单个本机 Modelica source-to-FMU 编译请求的声明性输入。"""

    source_path: str | Path
    model_name: str
    output_directory: str | Path
    compiler_path: str | Path | None = None
    fmi_version: str = "2.0"
    fmu_type: str = "cs"
    timeout_seconds: float = 120.0


@dataclass(frozen=True, slots=True)
class ModelicaCompileResult:
    """一次成功编译生成的 FMU 及其可核验摘要。"""

    fmu_path: str | Path
    fmi_version: str
    fmu_type: str
    compiler_version: str
    sha256: str
    diagnostics: tuple[ModelicaDiagnostic, ...] = ()
