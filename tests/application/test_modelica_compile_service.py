from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from farcel.application.engine import FarcelEngine
from farcel.application.modelica_service import ModelicaService
from farcel.contracts import (
    EngineError,
    ErrorCode,
    ModelicaCompileRequest,
    ModelicaCompileResult,
    ModelicaEnvironmentInfo,
    ModelicaEnvironmentStatus,
)


class _Importer:
    def load(self, path: Path):
        raise AssertionError(f"Unexpected model import: {path}")


class _Detector:
    def __init__(self) -> None:
        self.paths: list[str | Path | None] = []

    def detect(self, compiler_path: str | Path | None = None) -> ModelicaEnvironmentInfo:
        self.paths.append(compiler_path)
        return ModelicaEnvironmentInfo(
            ModelicaEnvironmentStatus.AVAILABLE,
            "OpenModelica",
            "1.26.3",
            "OpenModelica 编译器可用。",
        )


class _Compiler:
    def __init__(self, result: ModelicaCompileResult | EngineError) -> None:
        self.result = result
        self.requests: list[ModelicaCompileRequest] = []

    def compile(self, request: ModelicaCompileRequest) -> ModelicaCompileResult:
        self.requests.append(request)
        if isinstance(self.result, EngineError):
            raise self.result
        return self.result


class ModelicaCompileServiceTests(unittest.TestCase):
    def test_valid_request_delegates_and_preserves_result(self) -> None:
        with self._request_context() as request:
            expected = self._result()
            compiler = _Compiler(expected)

            result = ModelicaService(_Detector(), compiler).compile_modelica(request)

        self.assertIs(expected, result)
        self.assertEqual([request], compiler.requests)

    def test_invalid_request_is_rejected_before_compiler_call(self) -> None:
        with self._request_context(model_name="Bad); unsafe") as request:
            compiler = _Compiler(self._result())
            with self.assertRaises(EngineError) as raised:
                ModelicaService(_Detector(), compiler).compile_modelica(request)

        self.assertEqual(ErrorCode.CONFIG_ERROR, raised.exception.code)
        self.assertEqual("model_name", raised.exception.details["issues"][0]["field"])
        self.assertEqual([], compiler.requests)

    def test_compiler_error_is_preserved(self) -> None:
        with self._request_context() as request:
            expected = EngineError(ErrorCode.MODELICA_COMPILATION_ERROR, "模型检查失败")
            compiler = _Compiler(expected)
            with self.assertRaises(EngineError) as raised:
                ModelicaService(_Detector(), compiler).compile_modelica(request)

        self.assertIs(expected, raised.exception)
        self.assertEqual([request], compiler.requests)

    def test_missing_compiler_has_stable_error(self) -> None:
        with self._request_context() as request:
            with self.assertRaises(EngineError) as raised:
                ModelicaService(_Detector()).compile_modelica(request)

        self.assertEqual(ErrorCode.NOT_IMPLEMENTED, raised.exception.code)

    def test_engine_delegates_to_service_and_handles_absent_service(self) -> None:
        with self._request_context() as request:
            expected = self._result()
            compiler = _Compiler(expected)
            engine = FarcelEngine(
                _Importer(), modelica_service=ModelicaService(_Detector(), compiler)
            )

            self.assertIs(expected, engine.compile_modelica(request))
            with self.assertRaises(EngineError) as raised:
                FarcelEngine(_Importer()).compile_modelica(request)

        self.assertEqual(ErrorCode.NOT_IMPLEMENTED, raised.exception.code)
        self.assertEqual([request], compiler.requests)

    def test_environment_detection_remains_available_with_compiler(self) -> None:
        detector = _Detector()
        result = ModelicaService(detector, _Compiler(self._result())).get_environment("omc")

        self.assertTrue(result.is_available)
        self.assertEqual(["omc"], detector.paths)

    @staticmethod
    def _result() -> ModelicaCompileResult:
        return ModelicaCompileResult(
            fmu_path="result.fmu",
            fmi_version="2.0",
            fmu_type="cs",
            compiler_version="1.26.3",
            sha256="a" * 64,
        )

    @staticmethod
    def _request_context(model_name: str = "Example.Model"):
        class _Context:
            def __enter__(self) -> ModelicaCompileRequest:
                self._temporary_directory = tempfile.TemporaryDirectory()
                root = Path(self._temporary_directory.name)
                source = root / "Source.mo"
                source.write_text("model Source end Source;", encoding="utf-8")
                output = root / "output"
                output.mkdir()
                return ModelicaCompileRequest(source, model_name, output)

            def __exit__(self, *args: object) -> None:
                self._temporary_directory.cleanup()

        return _Context()
