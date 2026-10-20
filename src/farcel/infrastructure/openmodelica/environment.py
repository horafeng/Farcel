"""OpenModelica environment discovery and executable validation."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from farcel.contracts.modelica import (
    ModelicaEnvironmentInfo,
    ModelicaEnvironmentStatus,
)


_COMPILER_NAME = "OpenModelica"
_VERSION_PATTERN = re.compile(
    r"\bOpenModelica\s+v?([0-9]+(?:\.[0-9]+)+(?:[-+._A-Za-z0-9]*)?)",
    re.IGNORECASE,
)


class OpenModelicaEnvironmentDetector:
    """Discover OpenModelica and verify it with one ``--version`` process call."""

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        self._timeout_seconds = timeout_seconds

    def detect(
        self, compiler_path: str | Path | None = None
    ) -> ModelicaEnvironmentInfo:
        """Return a validated compiler summary without changing host environment state."""

        if compiler_path is not None:
            candidate = Path(compiler_path)
            if not candidate.is_file():
                return self._not_found("未找到指定的 OpenModelica 编译器。")
            return self._validate(candidate)

        candidate = self._discover_candidate()
        if candidate is None:
            return self._not_found("未找到 OpenModelica 编译器。")
        return self._validate(candidate)

    def _discover_candidate(self) -> Path | None:
        home = os.environ.get("OPENMODELICAHOME")
        if home:
            candidate = Path(home) / "bin" / self._executable_name()
            if candidate.is_file():
                return candidate

        for command in ("omc.exe", "omc") if os.name == "nt" else ("omc",):
            discovered = shutil.which(command)
            if discovered:
                candidate = Path(discovered)
                if candidate.is_file():
                    return candidate

        if os.name == "nt":
            for root in (Path("C:/"), Path("C:/Program Files")):
                for candidate in root.glob("OpenModelica*/bin/omc.exe"):
                    if candidate.is_file():
                        return candidate
        return None

    def _validate(self, candidate: Path) -> ModelicaEnvironmentInfo:
        try:
            completed = subprocess.run(
                [str(candidate), "--version"],
                check=False,
                shell=False,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return self._unusable("OpenModelica 编译器响应超时。")
        except OSError:
            return self._unusable("OpenModelica 编译器无法启动。")

        if completed.returncode != 0:
            return self._unusable("OpenModelica 编译器版本检查失败。")

        version = self._parse_version(completed.stdout)
        if version is None:
            return self._unusable("OpenModelica 编译器未返回可识别的版本信息。")
        return ModelicaEnvironmentInfo(
            status=ModelicaEnvironmentStatus.AVAILABLE,
            compiler_name=_COMPILER_NAME,
            compiler_version=version,
            diagnostic_message="OpenModelica 编译器可用。",
        )

    @staticmethod
    def _parse_version(output: str) -> str | None:
        match = _VERSION_PATTERN.search(output)
        return match.group(1) if match is not None else None

    @staticmethod
    def _not_found(message: str) -> ModelicaEnvironmentInfo:
        return ModelicaEnvironmentInfo(
            status=ModelicaEnvironmentStatus.NOT_FOUND,
            compiler_name=_COMPILER_NAME,
            compiler_version=None,
            diagnostic_message=message,
        )

    @staticmethod
    def _unusable(message: str) -> ModelicaEnvironmentInfo:
        return ModelicaEnvironmentInfo(
            status=ModelicaEnvironmentStatus.UNUSABLE,
            compiler_name=_COMPILER_NAME,
            compiler_version=None,
            diagnostic_message=message,
        )

    @staticmethod
    def _executable_name() -> str:
        return "omc.exe" if os.name == "nt" else "omc"
