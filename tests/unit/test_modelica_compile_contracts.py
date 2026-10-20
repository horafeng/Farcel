from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest

from farcel.contracts import (
    ModelicaCompileRequest,
    ModelicaCompileResult,
    ModelicaCompiler,
    ModelicaDiagnostic,
    ModelicaDiagnosticSeverity,
)


class _Compiler:
    def compile(self, request: ModelicaCompileRequest) -> ModelicaCompileResult:
        return ModelicaCompileResult(
            fmu_path=Path("output") / "Demo.fmu",
            fmi_version=request.fmi_version,
            fmu_type=request.fmu_type,
            compiler_version="1.26.3",
            sha256="a" * 64,
        )


class ModelicaCompileContractTests(unittest.TestCase):
    def test_request_defaults_describe_first_supported_compilation_shape(self) -> None:
        request = ModelicaCompileRequest("Demo.mo", "ExampleSystems.Demo", "output")

        self.assertEqual("2.0", request.fmi_version)
        self.assertEqual("cs", request.fmu_type)
        self.assertEqual(120.0, request.timeout_seconds)
        self.assertIsNone(request.compiler_path)

    def test_request_is_immutable_without_constructor_validation(self) -> None:
        request = ModelicaCompileRequest("", "", "")

        with self.assertRaises(FrozenInstanceError):
            request.model_name = "Demo"  # type: ignore[misc]
        self.assertEqual("", request.model_name)

    def test_diagnostic_and_result_keep_farcel_owned_immutable_values(self) -> None:
        diagnostic = ModelicaDiagnostic(
            ModelicaDiagnosticSeverity.WARNING,
            "SOURCE_NOTE",
            "示例诊断",
        )
        result = ModelicaCompileResult(
            "output/Demo.fmu",
            "2.0",
            "cs",
            "1.26.3",
            "b" * 64,
            (diagnostic,),
        )

        self.assertEqual(ModelicaDiagnosticSeverity.WARNING, result.diagnostics[0].severity)
        with self.assertRaises(FrozenInstanceError):
            result.sha256 = "c" * 64  # type: ignore[misc]

    def test_port_can_be_implemented_without_openmodelica_types(self) -> None:
        compiler: ModelicaCompiler = _Compiler()

        result = compiler.compile(ModelicaCompileRequest("Demo.mo", "Demo", "output"))

        self.assertEqual("Demo.fmu", Path(result.fmu_path).name)
        self.assertEqual("2.0", result.fmi_version)
