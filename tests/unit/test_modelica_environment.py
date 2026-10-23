from __future__ import annotations

from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from farcel.contracts import ModelicaEnvironmentInfo, ModelicaEnvironmentStatus
from farcel.infrastructure.openmodelica import OpenModelicaEnvironmentDetector


class OpenModelicaEnvironmentDetectorTests(unittest.TestCase):
    def test_info_is_immutable_and_derives_availability_from_status(self) -> None:
        info = ModelicaEnvironmentInfo(
            status=ModelicaEnvironmentStatus.AVAILABLE,
            compiler_name="OpenModelica",
            compiler_version="1.26.3",
            diagnostic_message="OpenModelica 编译器可用。",
        )

        self.assertTrue(info.is_available)
        with self.assertRaises(FrozenInstanceError):
            info.compiler_version = "other"  # type: ignore[misc]

    def test_explicit_valid_path_runs_version_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "omc.exe"
            executable.touch()
            expected_path = str(executable.resolve())
            with patch(
                "farcel.infrastructure.openmodelica.environment.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    [], 0, "OpenModelica v1.26.3 (64-bit)\n", ""
                ),
            ) as run:
                result = OpenModelicaEnvironmentDetector().detect(executable)

        self.assertEqual(ModelicaEnvironmentStatus.AVAILABLE, result.status)
        self.assertEqual("1.26.3", result.compiler_version)
        self.assertEqual([expected_path, "--version"], run.call_args.args[0])
        self.assertFalse(run.call_args.kwargs["shell"])

    def test_invalid_explicit_path_does_not_fall_back_to_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            environment_executable = home / "bin" / "omc.exe"
            environment_executable.parent.mkdir(parents=True)
            environment_executable.touch()
            with (
                patch.dict(os.environ, {"OPENMODELICAHOME": str(home)}),
                patch(
                    "farcel.infrastructure.openmodelica.environment.subprocess.run"
                ) as run,
            ):
                result = OpenModelicaEnvironmentDetector().detect(
                    Path(directory) / "missing-omc.exe"
                )

        self.assertEqual(ModelicaEnvironmentStatus.NOT_FOUND, result.status)
        self.assertIsNone(result.compiler_version)
        run.assert_not_called()

    def test_discovers_openmodelicahome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            executable = home / "bin" / "omc.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            with (
                patch.dict(os.environ, {"OPENMODELICAHOME": str(home)}),
                patch(
                    "farcel.infrastructure.openmodelica.environment.subprocess.run",
                    return_value=self._success(),
                ),
            ):
                result = OpenModelicaEnvironmentDetector().detect()

        self.assertTrue(result.is_available)
        self.assertEqual("1.26.3", result.compiler_version)

    def test_discovers_compiler_from_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "omc.exe"
            executable.touch()
            with (
                patch.dict(os.environ, {}, clear=True),
                patch(
                    "farcel.infrastructure.openmodelica.environment.shutil.which",
                    return_value=str(executable),
                ),
                patch(
                    "farcel.infrastructure.openmodelica.environment.subprocess.run",
                    return_value=self._success(),
                ),
            ):
                result = OpenModelicaEnvironmentDetector().detect()

        self.assertEqual(ModelicaEnvironmentStatus.AVAILABLE, result.status)

    def test_returns_not_found_without_candidate(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "farcel.infrastructure.openmodelica.environment.shutil.which",
                return_value=None,
            ),
            patch.object(Path, "glob", return_value=()),
        ):
            result = OpenModelicaEnvironmentDetector().detect()

        self.assertEqual(ModelicaEnvironmentStatus.NOT_FOUND, result.status)
        self.assertFalse(result.is_available)

    def test_reports_unusable_when_compiler_cannot_start(self) -> None:
        result = self._with_explicit_compiler(OSError("cannot start"))

        self.assertEqual(ModelicaEnvironmentStatus.UNUSABLE, result.status)
        self.assertEqual("OpenModelica 编译器无法启动。", result.diagnostic_message)

    def test_reports_unusable_when_version_command_fails(self) -> None:
        result = self._with_explicit_compiler(
            subprocess.CompletedProcess([], 3, "", "failure")
        )

        self.assertEqual(ModelicaEnvironmentStatus.UNUSABLE, result.status)
        self.assertEqual("OpenModelica 编译器版本检查失败。", result.diagnostic_message)

    def test_reports_unusable_when_version_command_times_out(self) -> None:
        result = self._with_explicit_compiler(
            subprocess.TimeoutExpired(["omc.exe", "--version"], 10)
        )

        self.assertEqual(ModelicaEnvironmentStatus.UNUSABLE, result.status)
        self.assertEqual("OpenModelica 编译器响应超时。", result.diagnostic_message)

    def test_reports_unusable_for_empty_or_unrecognised_version(self) -> None:
        for output in ("", "some other tool 4.0"):
            with self.subTest(output=output):
                result = self._with_explicit_compiler(
                    subprocess.CompletedProcess([], 0, output, "")
                )
                self.assertEqual(ModelicaEnvironmentStatus.UNUSABLE, result.status)
                self.assertEqual(
                    "OpenModelica 编译器未返回可识别的版本信息。",
                    result.diagnostic_message,
                )

    @staticmethod
    def _success() -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, "OpenModelica v1.26.3 (64-bit)\n", "")

    @staticmethod
    def _with_explicit_compiler(
        side_effect: subprocess.CompletedProcess[str] | BaseException,
    ) -> ModelicaEnvironmentInfo:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "omc.exe"
            executable.touch()
            mock_arguments = (
                {"side_effect": side_effect}
                if isinstance(side_effect, BaseException)
                else {"return_value": side_effect}
            )
            with patch(
                "farcel.infrastructure.openmodelica.environment.subprocess.run",
                **mock_arguments,
            ):
                return OpenModelicaEnvironmentDetector().detect(executable)
