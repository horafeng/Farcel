from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from farcel.cli import main
from farcel.contracts import (
    EngineError,
    ErrorCode,
    ModelicaCompileResult,
    ModelicaEnvironmentInfo,
    ModelicaEnvironmentStatus,
)


class _Backend:
    def __init__(self) -> None:
        self.environment = ModelicaEnvironmentInfo(
            ModelicaEnvironmentStatus.AVAILABLE,
            "OpenModelica",
            "1.26.3",
            "OpenModelica 编译器可用。",
        )
        self.compile_result = ModelicaCompileResult(
            Path("output/FirstOrder.fmu"), "2.0", "cs", "1.26.3", "a" * 64
        )
        self.environment_paths: list[object] = []
        self.compile_requests: list[object] = []
        self.compile_error: EngineError | None = None

    def get_modelica_environment(self, compiler_path=None):
        self.environment_paths.append(compiler_path)
        return self.environment

    def compile_modelica(self, request):
        self.compile_requests.append(request)
        if self.compile_error is not None:
            raise self.compile_error
        return self.compile_result

    def compile_project_modelica(self, *args, **kwargs):
        raise AssertionError("CLI 不得调用 Project 编译 API")


class ModelicaCliTests(unittest.TestCase):
    def test_root_and_modelica_help_keep_zero_exit_code(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, main(()))
        self.assertIn("usage:", output.getvalue())

        for arguments in (("modelica", "--help"), ("modelica", "env", "--help"), ("modelica", "compile", "--help")):
            output = StringIO()
            with self.assertRaises(SystemExit) as raised, redirect_stdout(output):
                main(arguments)
            self.assertEqual(0, raised.exception.code)
            self.assertIn("usage:", output.getvalue())

    def test_missing_modelica_subcommand_is_argparse_error(self) -> None:
        stderr = StringIO()
        with self.assertRaises(SystemExit) as raised, redirect_stderr(stderr):
            main(("modelica",))
        self.assertEqual(2, raised.exception.code)
        self.assertIn("需要指定子命令", stderr.getvalue())

    def test_compile_missing_required_arguments_is_argparse_error(self) -> None:
        stderr = StringIO()
        with self.assertRaises(SystemExit) as raised, redirect_stderr(stderr):
            main(("modelica", "compile", "source.mo"))
        self.assertEqual(2, raised.exception.code)
        self.assertIn("--model", stderr.getvalue())
        self.assertIn("--output-dir", stderr.getvalue())

    def test_env_uses_public_backend_and_has_plain_and_json_output(self) -> None:
        backend = _Backend()
        with patch("farcel.cli.create_backend", return_value=backend):
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = main(("modelica", "env", "--omc", "C:/路径/omc.exe"))
            self.assertEqual(0, code)
            self.assertEqual(["C:/路径/omc.exe"], backend.environment_paths)
            self.assertIn("状态: AVAILABLE", stdout.getvalue())

            stdout = StringIO()
            with redirect_stdout(stdout):
                code = main(("modelica", "env", "--json"))
        self.assertEqual(0, code)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(True, payload["success"])
        self.assertEqual("AVAILABLE", payload["status"])
        self.assertEqual("1.26.3", payload["compiler_version"])

    def test_env_not_found_and_unusable_return_one_without_error_protocol(self) -> None:
        backend = _Backend()
        for status in (ModelicaEnvironmentStatus.NOT_FOUND, ModelicaEnvironmentStatus.UNUSABLE):
            backend.environment = ModelicaEnvironmentInfo(status, "OpenModelica", None, "不可用")
            stdout = StringIO()
            with patch("farcel.cli.create_backend", return_value=backend), redirect_stdout(stdout):
                code = main(("modelica", "env", "--json"))
            payload = json.loads(stdout.getvalue())
            self.assertEqual(1, code)
            self.assertFalse(payload["success"])
            self.assertEqual(status.name, payload["status"])

    def test_compile_builds_existing_request_and_prints_plain_and_json_success(self) -> None:
        backend = _Backend()
        arguments = (
            "modelica", "compile", "模型 空格/First Order.mo", "--model", "Example.FirstOrder",
            "--output-dir", "输出 目录", "--omc", "C:/Program Files/OpenModelica/bin/omc.exe",
            "--timeout", "42.5",
        )
        with patch("farcel.cli.create_backend", return_value=backend):
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = main(arguments)
            self.assertEqual(0, code)
            request = backend.compile_requests[-1]
            self.assertEqual("模型 空格/First Order.mo", request.source_path)
            self.assertEqual("Example.FirstOrder", request.model_name)
            self.assertEqual("输出 目录", request.output_directory)
            self.assertEqual(42.5, request.timeout_seconds)
            self.assertIn("编译完成: 是", stdout.getvalue())

            stdout = StringIO()
            with redirect_stdout(stdout):
                code = main(arguments + ("--json",))
        self.assertEqual(0, code)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(True, payload["success"])
        self.assertEqual(str(Path("output/FirstOrder.fmu")), payload["fmu_path"])
        self.assertEqual("a" * 64, payload["sha256"])

    def test_compile_errors_are_stable_and_json_stdout_remains_empty(self) -> None:
        backend = _Backend()
        backend.compile_error = EngineError(
            ErrorCode.CONFIG_ERROR,
            "Modelica 编译请求无效",
            {
                "issues": ({"field": "model_name", "code": "INVALID_MODEL_NAME", "message": "无效"},),
                "path": Path("输出 目录"),
            },
        )
        stdout, stderr = StringIO(), StringIO()
        with patch("farcel.cli.create_backend", return_value=backend), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(("modelica", "compile", "source.mo", "--model", "bad;name", "--output-dir", "out", "--json"))
        self.assertEqual(1, code)
        self.assertEqual("", stdout.getvalue())
        payload = json.loads(stderr.getvalue())
        self.assertFalse(payload["success"])
        self.assertEqual("CONFIG_ERROR", payload["error"]["code"])
        self.assertEqual("INVALID_MODEL_NAME", payload["error"]["details"]["issues"][0]["code"])
        self.assertEqual(str(Path("输出 目录")), payload["error"]["details"]["path"])

    def test_compile_runtime_error_codes_remain_distinct(self) -> None:
        for code in (
            ErrorCode.MODELICA_COMPILER_NOT_FOUND,
            ErrorCode.MODELICA_OUTPUT_CONFLICT,
            ErrorCode.TIMEOUT,
        ):
            backend = _Backend()
            backend.compile_error = EngineError(code, "失败")
            stderr = StringIO()
            with patch("farcel.cli.create_backend", return_value=backend), redirect_stderr(stderr):
                exit_code = main(("modelica", "compile", "source.mo", "--model", "M", "--output-dir", "out"))
            self.assertEqual(1, exit_code)
            self.assertIn(code.value, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
