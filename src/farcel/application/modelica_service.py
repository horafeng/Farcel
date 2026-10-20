"""Application use case for Modelica compiler environment detection."""

from __future__ import annotations

from pathlib import Path

from farcel.contracts.modelica import ModelicaEnvironmentInfo
from farcel.contracts.ports import ModelicaEnvironmentDetector


class ModelicaService:
    """将环境查询委托给实现无关的 Modelica detector port。"""

    def __init__(self, detector: ModelicaEnvironmentDetector) -> None:
        self._detector = detector

    def get_environment(
        self, compiler_path: str | Path | None = None
    ) -> ModelicaEnvironmentInfo:
        """查询指定或自动发现的 Modelica 编译器环境。"""

        return self._detector.detect(compiler_path=compiler_path)
