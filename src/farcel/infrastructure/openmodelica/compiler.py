"""OpenModelica implementation of Farcel's ModelicaCompiler port."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import tempfile
import xml.etree.ElementTree as ElementTree
import zipfile

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.modelica import (
    ModelicaCompileRequest,
    ModelicaCompileResult,
    ModelicaDiagnostic,
    ModelicaDiagnosticSeverity,
    ModelicaEnvironmentStatus,
)
from farcel.contracts.modelica_validation import (
    modelica_compile_request_issue_details,
    validate_modelica_compile_request,
)
from farcel.infrastructure.openmodelica.environment import OpenModelicaEnvironmentDetector


_HASH_CHUNK_SIZE = 1024 * 1024
_MODEL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class OpenModelicaCompiler:
    """Compile one validated Modelica source into a verified FMI 2.0 CS FMU."""

    def __init__(
        self,
        environment_detector: OpenModelicaEnvironmentDetector | None = None,
    ) -> None:
        self._environment_detector = environment_detector or OpenModelicaEnvironmentDetector()

    def compile(self, request: ModelicaCompileRequest) -> ModelicaCompileResult:
        """Compile, validate and publish one FMU without overwriting outputs."""

        _require_valid_modelica_compile_request(request)
        omc_path, compiler_version = self._resolve_compiler(request)
        with tempfile.TemporaryDirectory(prefix="farcel-openmodelica-") as directory:
            workspace = Path(directory)
            source_fmu = self._compile_in_workspace(
                request, omc_path, workspace
            )
            self._validate_fmu(source_fmu)
            source_hash = _sha256(source_fmu)
            destination = self._publish_fmu(source_fmu, Path(request.output_directory))
            destination_hash = _sha256(destination)
            if destination_hash != source_hash:
                _remove_current_file(destination)
                raise EngineError(
                    ErrorCode.MODELICA_OUTPUT_ERROR,
                    "FMU 发布后的 SHA-256 校验失败",
                    {"stage": "publish_hash"},
                )
            return ModelicaCompileResult(
                fmu_path=destination,
                fmi_version=request.fmi_version,
                fmu_type=request.fmu_type,
                compiler_version=compiler_version,
                sha256=destination_hash,
                diagnostics=(
                    ModelicaDiagnostic(
                        ModelicaDiagnosticSeverity.INFO,
                        "MODELICA_FMU_COMPILED",
                        "OpenModelica 已成功生成并验证 FMI 2.0 Co-Simulation FMU。",
                    ),
                ),
            )

    def _resolve_compiler(self, request: ModelicaCompileRequest) -> tuple[Path, str]:
        path = self._environment_detector.resolve_compiler_path(request.compiler_path)
        if path is None:
            raise EngineError(
                ErrorCode.MODELICA_COMPILER_NOT_FOUND,
                "未找到可用的 OpenModelica 编译器。",
                {"stage": "compiler_discovery"},
            )
        environment = self._environment_detector.detect(path)
        if environment.status is not ModelicaEnvironmentStatus.AVAILABLE:
            raise EngineError(
                ErrorCode.MODELICA_COMPILER_UNUSABLE,
                "OpenModelica 编译器无法正常启动。",
                {"stage": "compiler_version"},
            )
        if environment.compiler_version is None:
            raise EngineError(
                ErrorCode.MODELICA_COMPILER_UNUSABLE,
                "OpenModelica 编译器未返回有效版本信息。",
                {"stage": "compiler_version"},
            )
        return path, environment.compiler_version

    def _compile_in_workspace(
        self,
        request: ModelicaCompileRequest,
        omc_path: Path,
        workspace: Path,
    ) -> Path:
        script = self._write_script(request, workspace)
        output = self._run_omc(omc_path, script, workspace, request.timeout_seconds)
        if _find_marker(output, "FARCEL_LOAD_FILE") != "true":
            raise _compilation_error("load", "OpenModelica 未能加载 Modelica 源文件。")
        expected_check = f"Check of {request.model_name} completed successfully."
        if _find_marker(output, "FARCEL_CHECK_MODEL") != expected_check:
            raise _compilation_error("check", "OpenModelica 模型检查未成功完成。")
        reported_path = _find_marker(output, "FARCEL_FMU_PATH")
        if not reported_path:
            raise _compilation_error("build", "OpenModelica 未报告 FMU 产物。")
        fmu_path = Path(reported_path)
        if not fmu_path.is_absolute():
            fmu_path = workspace / fmu_path
        if not fmu_path.is_file() or fmu_path.stat().st_size == 0:
            raise _compilation_error("build", "OpenModelica 未生成有效的 FMU 文件。")
        return fmu_path.resolve()

    def _write_script(self, request: ModelicaCompileRequest, workspace: Path) -> Path:
        source_path = _modelica_string(Path(request.source_path))
        workspace_path = _modelica_string(workspace)
        script_path = workspace / "compile_modelica.mos"
        script_path.write_text(
            "\n".join(
                (
                    f'cd("{workspace_path}");',
                    f'loadOk := loadFile("{source_path}");',
                    'print("FARCEL_LOAD_FILE=" + String(loadOk) + "\\n");',
                    f'checkResult := checkModel({request.model_name});',
                    'print("FARCEL_CHECK_MODEL=" + checkResult + "\\n");',
                    (
                        f'fmuResult := buildModelFMU({request.model_name}, '
                        'version="2.0", fmuType="cs");'
                    ),
                    'print("FARCEL_FMU_PATH=" + fmuResult + "\\n");',
                    'print("FARCEL_DIAGNOSTICS_BEGIN\\n" + getErrorString() '
                    '+ "FARCEL_DIAGNOSTICS_END\\n");',
                    "exit();",
                    "",
                )
            ),
            encoding="utf-8",
        )
        return script_path

    def _run_omc(
        self,
        omc_path: Path,
        script_path: Path,
        workspace: Path,
        timeout_seconds: float,
    ) -> str:
        popen_arguments: dict[str, object] = {
            "cwd": workspace,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "shell": False,
        }
        if os.name == "nt":
            popen_arguments["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_arguments["start_new_session"] = True
        try:
            process = subprocess.Popen([str(omc_path), str(script_path)], **popen_arguments)
        except OSError:
            raise EngineError(
                ErrorCode.MODELICA_COMPILER_UNUSABLE,
                "OpenModelica 编译器无法启动。",
                {"stage": "compiler_start"},
            ) from None
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            self._terminate_process_tree(process)
            raise EngineError(
                ErrorCode.TIMEOUT,
                "OpenModelica 编译超时。",
                {"stage": "compile_timeout", "timeout_seconds": timeout_seconds},
            ) from None
        output = f"{stdout}{stderr}"
        if process.returncode != 0:
            raise _compilation_error("process", "OpenModelica 编译进程异常退出。")
        return output

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        """Best-effort cleanup for the OMC process and compiler children started by it."""

        if process.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.communicate(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass

    @staticmethod
    def _validate_fmu(fmu_path: Path) -> None:
        try:
            with zipfile.ZipFile(fmu_path) as archive:
                names = set(archive.namelist())
                if "modelDescription.xml" not in names:
                    raise ValueError("FMU 缺少 modelDescription.xml")
                root = ElementTree.fromstring(archive.read("modelDescription.xml"))
                if root.attrib.get("fmiVersion") != "2.0":
                    raise ValueError("FMU 不是 FMI 2.0")
                co_simulation = root.find("{*}CoSimulation")
                if co_simulation is None:
                    raise ValueError("FMU 不包含 CoSimulation 接口")
                model_identifier = co_simulation.attrib.get("modelIdentifier", "")
                if _MODEL_IDENTIFIER_PATTERN.fullmatch(model_identifier) is None:
                    raise ValueError("FMU 的 CoSimulation modelIdentifier 无效")
                if (
                    os.name == "nt"
                    and platform.machine().lower() in {"amd64", "x86_64"}
                    and f"binaries/win64/{model_identifier}.dll" not in names
                ):
                    raise ValueError("FMU 缺少当前 Windows x64 Co-Simulation 原生 DLL")
        except (OSError, ValueError, zipfile.BadZipFile, ElementTree.ParseError):
            raise EngineError(
                ErrorCode.MODELICA_FMU_INVALID,
                "OpenModelica 生成的 FMU 产物无效。",
                {"stage": "fmu_validation", "reason": "FMU 结构检查未通过"},
            ) from None

    @staticmethod
    def _publish_fmu(source: Path, output_directory: Path) -> Path:
        destination = output_directory / source.name
        try:
            with source.open("rb") as input_file, destination.open("xb") as output_file:
                shutil.copyfileobj(input_file, output_file, _HASH_CHUNK_SIZE)
        except FileExistsError:
            raise EngineError(
                ErrorCode.MODELICA_OUTPUT_CONFLICT,
                "目标目录已存在同名 FMU，拒绝覆盖。",
                {"stage": "publish", "filename": source.name},
            ) from None
        except OSError:
            _remove_current_file(destination)
            raise EngineError(
                ErrorCode.MODELICA_OUTPUT_ERROR,
                "无法将已验证 FMU 发布到目标目录。",
                {"stage": "publish", "filename": source.name},
            ) from None
        return destination


def _modelica_string(path: Path) -> str:
    value = str(path.resolve())
    if any(ord(character) < 32 for character in value):
        raise EngineError(
            ErrorCode.CONFIG_ERROR,
            "Modelica 编译路径包含不允许的控制字符。",
            {"stage": "script_generation"},
        )
    return value.replace("\\", "/").replace('"', '\\"')


def _find_marker(output: str, marker: str) -> str | None:
    prefix = f"{marker}="
    for line in output.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def _compilation_error(stage: str, message: str) -> EngineError:
    return EngineError(ErrorCode.MODELICA_COMPILATION_ERROR, message, {"stage": stage})


def _require_valid_modelica_compile_request(
    request: ModelicaCompileRequest,
) -> None:
    """Preserve CONFIG_ERROR semantics for direct, untrusted adapter callers."""

    report = validate_modelica_compile_request(request)
    if not report.is_valid:
        raise EngineError(
            ErrorCode.CONFIG_ERROR,
            "Modelica 编译请求验证失败",
            {"issues": modelica_compile_request_issue_details(report)},
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        while chunk := artifact.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_current_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
