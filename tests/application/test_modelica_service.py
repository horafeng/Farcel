from __future__ import annotations

from pathlib import Path
import unittest

from farcel.application.engine import FarcelEngine
from farcel.application.modelica_service import ModelicaService
from farcel.contracts import ModelicaEnvironmentInfo, ModelicaEnvironmentStatus


class _Importer:
    def load(self, path: Path):
        raise AssertionError(f"Unexpected model import: {path}")


class _Detector:
    def __init__(self, result: ModelicaEnvironmentInfo) -> None:
        self.result = result
        self.paths: list[str | Path | None] = []

    def detect(self, compiler_path: str | Path | None = None) -> ModelicaEnvironmentInfo:
        self.paths.append(compiler_path)
        return self.result


class ModelicaServiceTests(unittest.TestCase):
    def test_service_delegates_the_path_and_returns_contract_value(self) -> None:
        expected = ModelicaEnvironmentInfo(
            ModelicaEnvironmentStatus.AVAILABLE,
            "OpenModelica",
            "1.26.3",
            "OpenModelica 编译器可用。",
        )
        detector = _Detector(expected)

        result = ModelicaService(detector).get_environment("compiler path")

        self.assertIs(expected, result)
        self.assertEqual(["compiler path"], detector.paths)

    def test_engine_without_injected_service_has_stable_unusable_result(self) -> None:
        result = FarcelEngine(_Importer()).get_modelica_environment()

        self.assertEqual(ModelicaEnvironmentStatus.UNUSABLE, result.status)
        self.assertFalse(result.is_available)
        self.assertEqual("未配置 Modelica 环境检测服务。", result.diagnostic_message)

    def test_engine_delegates_to_injected_service(self) -> None:
        expected = ModelicaEnvironmentInfo(
            ModelicaEnvironmentStatus.NOT_FOUND,
            "OpenModelica",
            None,
            "未找到 OpenModelica 编译器。",
        )
        detector = _Detector(expected)
        engine = FarcelEngine(_Importer(), modelica_service=ModelicaService(detector))

        result = engine.get_modelica_environment(Path("custom-omc"))

        self.assertIs(expected, result)
        self.assertEqual([Path("custom-omc")], detector.paths)
