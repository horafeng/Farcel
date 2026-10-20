"""Application workflows for Modelica compiler discovery and compilation."""

from __future__ import annotations

from pathlib import Path

from farcel.application.modelica_validation import require_valid_modelica_compile_request
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.modelica import (
    ModelicaCompileRequest,
    ModelicaCompileResult,
    ModelicaEnvironmentInfo,
)
from farcel.contracts.ports import ModelicaCompiler, ModelicaEnvironmentDetector


class ModelicaService:
    """将环境查询委托给实现无关的 Modelica detector port。"""

    def __init__(
        self,
        detector: ModelicaEnvironmentDetector,
        compiler: ModelicaCompiler | None = None,
    ) -> None:
        self._detector = detector
        self._compiler = compiler

    def get_environment(
        self, compiler_path: str | Path | None = None
    ) -> ModelicaEnvironmentInfo:
        """查询指定或自动发现的 Modelica 编译器环境。"""

        return self._detector.detect(compiler_path=compiler_path)

    def compile_modelica(
        self, request: ModelicaCompileRequest
    ) -> ModelicaCompileResult:
        """Validate and delegate one Modelica-to-FMU request through the compiler port."""

        require_valid_modelica_compile_request(request)
        if self._compiler is None:
            raise EngineError(ErrorCode.NOT_IMPLEMENTED, "未配置 Modelica 编译器服务。")
        return self._compiler.compile(request)
