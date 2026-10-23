from __future__ import annotations

import os
from pathlib import Path
import unittest

from farcel import create_backend
from farcel.contracts import ModelicaEnvironmentInfo, ModelicaEnvironmentStatus


class ModelicaEnvironmentPublicApiTests(unittest.TestCase):
    def test_default_backend_returns_not_found_for_invalid_explicit_path(self) -> None:
        backend = create_backend()

        result = backend.get_modelica_environment("missing-explicit-omc.exe")

        self.assertIsInstance(result, ModelicaEnvironmentInfo)
        self.assertEqual(ModelicaEnvironmentStatus.NOT_FOUND, result.status)
        self.assertFalse(result.is_available)

    @unittest.skipUnless(
        os.environ.get("FARCEL_RUN_OPENMODELICA_INTEGRATION") == "1",
        "需要显式设置 FARCEL_RUN_OPENMODELICA_INTEGRATION=1 并安装 OpenModelica",
    )
    def test_real_openmodelica_environment_is_available(self) -> None:
        compiler_path = Path(
            os.environ.get("FARCEL_OPENMODELICA_OMC", r"D:\OpenModelica\bin\omc.exe")
        )
        backend = create_backend()

        result = backend.get_modelica_environment(compiler_path)

        self.assertEqual(ModelicaEnvironmentStatus.AVAILABLE, result.status)
        self.assertTrue(result.is_available)
        self.assertEqual("OpenModelica", result.compiler_name)
        self.assertEqual("1.26.3", result.compiler_version)
