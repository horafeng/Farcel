from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from farcel.contracts import (
    EngineError,
    ErrorCode,
    ModelicaCompileRequest,
    ModelicaEnvironmentInfo,
    ModelicaEnvironmentStatus,
)
from farcel.infrastructure.openmodelica.compiler import OpenModelicaCompiler


class _Detector:
    def __init__(self, path: Path | None = Path("omc.exe")) -> None:
        self.path = path
        self.requested_paths: list[object] = []

    def resolve_compiler_path(self, compiler_path=None):
        self.requested_paths.append(compiler_path)
        return self.path

    def detect(self, compiler_path=None) -> ModelicaEnvironmentInfo:
        return ModelicaEnvironmentInfo(
            ModelicaEnvironmentStatus.AVAILABLE,
            "OpenModelica",
            "1.26.3",
            "OpenModelica 编译器可用。",
        )


class _Process:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.pid = 1234

    def communicate(self, timeout=None):
        return self._stdout, self._stderr

    def poll(self):
        return self.returncode


class OpenModelicaCompilerTests(unittest.TestCase):
    def test_successfully_publishes_verified_fmu_and_sha256(self) -> None:
        with self._request_context() as (request, output):
            compiler = OpenModelicaCompiler(_Detector())
            self._install_successful_omc_output(compiler, "Generated.fmu")

            result = compiler.compile(request)
            self.assertEqual(output / "Generated.fmu", result.fmu_path)
            self.assertTrue(result.fmu_path.is_file())
            self.assertEqual(self._sha256(result.fmu_path), result.sha256)
            self.assertEqual("1.26.3", result.compiler_version)
            self.assertEqual("MODELICA_FMU_COMPILED", result.diagnostics[0].code)

    def test_explicit_path_is_forwarded_and_invalid_path_is_not_found(self) -> None:
        with self._request_context(compiler_path="custom omc.exe") as (request, _):
            detector = _Detector()
            compiler = OpenModelicaCompiler(detector)
            self._install_successful_omc_output(compiler, "Generated.fmu")
            compiler.compile(request)
            self.assertEqual(["custom omc.exe"], detector.requested_paths)

        with self._request_context() as (request, _):
            with self.assertRaises(EngineError) as raised:
                OpenModelicaCompiler(_Detector(None)).compile(request)
        self.assertEqual(ErrorCode.MODELICA_COMPILER_NOT_FOUND, raised.exception.code)

    def test_automatic_discovery_is_used_when_compiler_path_is_omitted(self) -> None:
        with self._request_context() as (request, _):
            detector = _Detector()
            compiler = OpenModelicaCompiler(detector)
            self._install_successful_omc_output(compiler, "Generated.fmu")
            compiler.compile(request)

        self.assertEqual([None], detector.requested_paths)

    def test_direct_adapter_rejects_an_unsafe_model_name_before_discovery(self) -> None:
        with self._request_context(model_name="Safe); system(\"unexpected\")") as (request, _):
            detector = _Detector()
            with self.assertRaises(EngineError) as raised:
                OpenModelicaCompiler(detector).compile(request)

        self.assertEqual(ErrorCode.CONFIG_ERROR, raised.exception.code)
        self.assertEqual("model_name", raised.exception.details["issues"][0]["field"])
        self.assertEqual([], detector.requested_paths)

    def test_unusable_compiler_and_start_failure_have_stable_errors(self) -> None:
        class _UnusableDetector(_Detector):
            def detect(self, compiler_path=None) -> ModelicaEnvironmentInfo:
                return ModelicaEnvironmentInfo(
                    ModelicaEnvironmentStatus.UNUSABLE,
                    "OpenModelica",
                    None,
                    "无法启动",
                )

        with self._request_context() as (request, _):
            with self.assertRaises(EngineError) as unusable:
                OpenModelicaCompiler(_UnusableDetector()).compile(request)
        self.assertEqual(ErrorCode.MODELICA_COMPILER_UNUSABLE, unusable.exception.code)

        compiler = OpenModelicaCompiler(_Detector())
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "compile.mos"
            script.touch()
            with patch(
                "farcel.infrastructure.openmodelica.compiler.subprocess.Popen",
                side_effect=OSError("cannot start"),
            ):
                with self.assertRaises(EngineError) as start_failure:
                    compiler._run_omc(Path("omc.exe"), script, Path(directory), 1.0)
        self.assertEqual(ErrorCode.MODELICA_COMPILER_UNUSABLE, start_failure.exception.code)
        self.assertEqual("compiler_start", start_failure.exception.details["stage"])

    def test_load_check_process_and_missing_artifact_fail_with_stable_stages(self) -> None:
        cases = (
            ("FARCEL_LOAD_FILE=false\n", "load"),
            ("FARCEL_LOAD_FILE=true\nFARCEL_CHECK_MODEL=failed\n", "check"),
            (
                "FARCEL_LOAD_FILE=true\n"
                "FARCEL_CHECK_MODEL=Check of Example.System completed successfully.\n",
                "build",
            ),
        )
        for output, stage in cases:
            with self.subTest(stage=stage), self._request_context(model_name="Example.System") as (request, _):
                compiler = OpenModelicaCompiler(_Detector())
                compiler._run_omc = lambda *args, output=output: output  # type: ignore[method-assign]
                with self.assertRaises(EngineError) as raised:
                    compiler.compile(request)
                self.assertEqual(ErrorCode.MODELICA_COMPILATION_ERROR, raised.exception.code)
                self.assertEqual(stage, raised.exception.details["stage"])

    def test_nonzero_process_exit_and_timeout_are_mapped(self) -> None:
        compiler = OpenModelicaCompiler(_Detector())
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "compile.mos"
            script.touch()
            with patch(
                "farcel.infrastructure.openmodelica.compiler.subprocess.Popen",
                return_value=_Process(returncode=3),
            ):
                with self.assertRaises(EngineError) as exited:
                    compiler._run_omc(Path("omc.exe"), script, Path(directory), 1.0)
        self.assertEqual(ErrorCode.MODELICA_COMPILATION_ERROR, exited.exception.code)
        self.assertEqual("process", exited.exception.details["stage"])

        class _TimeoutProcess(_Process):
            def communicate(self, timeout=None):
                raise subprocess.TimeoutExpired("omc", timeout)

        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "compile.mos"
            script.touch()
            with (
                patch(
                    "farcel.infrastructure.openmodelica.compiler.subprocess.Popen",
                    return_value=_TimeoutProcess(),
                ),
                patch.object(compiler, "_terminate_process_tree") as cleanup,
            ):
                with self.assertRaises(EngineError) as timed_out:
                    compiler._run_omc(Path("omc.exe"), script, Path(directory), 1.0)
        self.assertEqual(ErrorCode.TIMEOUT, timed_out.exception.code)
        cleanup.assert_called_once()

    def test_invalid_fmu_structures_are_rejected(self) -> None:
        invalid_cases = (
            (b"not a zip", None),
            (None, "3.0"),
            (None, "missing-cs"),
            (None, "missing-dll"),
        )
        for content, variant in invalid_cases:
            with self.subTest(variant=variant), self._request_context() as (request, _):
                compiler = OpenModelicaCompiler(_Detector())

                def run(*args, content=content, variant=variant):
                    workspace = args[2]
                    artifact = workspace / "Generated.fmu"
                    if content is not None:
                        artifact.write_bytes(content)
                    else:
                        self._write_fmu(artifact, variant=variant)
                    return self._success_output("Generated.fmu", request.model_name)

                compiler._run_omc = run  # type: ignore[method-assign]
                with self.assertRaises(EngineError) as raised:
                    compiler.compile(request)
                self.assertEqual(ErrorCode.MODELICA_FMU_INVALID, raised.exception.code)

    def test_existing_target_is_never_overwritten(self) -> None:
        with self._request_context() as (request, output):
            existing = output / "Generated.fmu"
            existing.write_bytes(b"preserve")
            compiler = OpenModelicaCompiler(_Detector())
            self._install_successful_omc_output(compiler, "Generated.fmu")
            with self.assertRaises(EngineError) as raised:
                compiler.compile(request)
            self.assertEqual(b"preserve", existing.read_bytes())

        self.assertEqual(ErrorCode.MODELICA_OUTPUT_CONFLICT, raised.exception.code)

    def test_failure_cleans_the_isolated_workspace(self) -> None:
        observed_workspaces: list[Path] = []
        with self._request_context() as (request, _):
            compiler = OpenModelicaCompiler(_Detector())

            def failing(*args):
                observed_workspaces.append(args[2])
                return "FARCEL_LOAD_FILE=false\n"

            compiler._run_omc = failing  # type: ignore[method-assign]
            with self.assertRaises(EngineError):
                compiler.compile(request)

        self.assertEqual(1, len(observed_workspaces))
        self.assertFalse(observed_workspaces[0].exists())

    def test_script_supports_unicode_space_paths_and_qualified_model_names(self) -> None:
        with tempfile.TemporaryDirectory(prefix="空 格-") as directory:
            root = Path(directory)
            source = root / "模型 source.mo"
            source.write_text("model Source end Source;", encoding="utf-8")
            output = root / "output"
            output.mkdir()
            request = ModelicaCompileRequest(source, "ExampleSystems.FirstOrder", output)
            script = OpenModelicaCompiler(_Detector())._write_script(request, root)
            content = script.read_text(encoding="utf-8")
            self.assertIn("checkModel(ExampleSystems.FirstOrder)", content)
            self.assertIn('fmuType="cs"', content)

    @staticmethod
    def _success_output(filename: str, model_name: str) -> str:
        return "\n".join(
            (
                "FARCEL_LOAD_FILE=true",
                f"FARCEL_CHECK_MODEL=Check of {model_name} completed successfully.",
                f"FARCEL_FMU_PATH={filename}",
            )
        )

    def _install_successful_omc_output(self, compiler: OpenModelicaCompiler, filename: str) -> None:
        def run(*args):
            workspace = args[2]
            self._write_fmu(workspace / filename)
            script = args[1]
            text = script.read_text(encoding="utf-8")
            model_name = text.split("checkModel(", 1)[1].split(")", 1)[0]
            return self._success_output(filename, model_name)

        compiler._run_omc = run  # type: ignore[method-assign]

    @staticmethod
    def _write_fmu(path: Path, variant: str | None = None) -> None:
        fmi_version = "3.0" if variant == "3.0" else "2.0"
        co_simulation = "" if variant == "missing-cs" else '<CoSimulation modelIdentifier="Generated"/>'
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "modelDescription.xml",
                f'<fmiModelDescription fmiVersion="{fmi_version}">{co_simulation}</fmiModelDescription>',
            )
            if variant != "missing-dll":
                archive.writestr("binaries/win64/Generated.dll", b"native")

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _request_context(
        model_name: str = "Example.System", compiler_path=None
    ):
        class _Context:
            def __enter__(self):
                self._temporary_directory = tempfile.TemporaryDirectory()
                root = Path(self._temporary_directory.name)
                source = root / "Source.mo"
                source.write_text("model Source end Source;", encoding="utf-8")
                output = root / "output"
                output.mkdir()
                return ModelicaCompileRequest(source, model_name, output, compiler_path), output

            def __exit__(self, *args):
                self._temporary_directory.cleanup()

        return _Context()
