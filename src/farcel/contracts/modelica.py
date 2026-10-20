"""Modelica compiler environment contracts owned by Farcel."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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
