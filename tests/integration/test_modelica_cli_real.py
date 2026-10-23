"""Opt-in validation of the installed Modelica CLI entry point."""

from __future__ import annotations

import hashlib
import json
import locale
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from farcel import create_backend
from farcel.contracts import SimulationConfig, SimulationState


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MODEL = REPOSITORY_ROOT / "examples" / "modelica" / "FarcelFirstOrder.mo"
DEFAULT_OMC = Path(r"D:\OpenModelica\bin\omc.exe")


@unittest.skipUnless(
    os.environ.get("FARCEL_RUN_OPENMODELICA_INTEGRATION") == "1",
    "需要显式设置 FARCEL_RUN_OPENMODELICA_INTEGRATION=1 并安装 OpenModelica",
)
class ModelicaCliRealIntegrationTests(unittest.TestCase):
    def test_installed_cli_env_compile_inspect_run_and_conflict(self) -> None:
        cli = shutil.which("farcel")
        self.assertIsNotNone(cli, "未找到已安装的 farcel CLI 入口")
        compiler_path = Path(os.environ.get("FARCEL_OPENMODELICA_OMC", DEFAULT_OMC))
        self.assertTrue(compiler_path.is_file(), f"未找到 OMC: {compiler_path}")

        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT / "artifacts") as directory:
            output = Path(directory) / "output with space"
            output.mkdir()
            environment = _run(cli, "modelica", "env", "--omc", str(compiler_path), "--json")
            self.assertEqual(0, environment.returncode, environment.stderr)
            environment_payload = json.loads(environment.stdout)
            self.assertTrue(environment_payload["success"])
            self.assertEqual("AVAILABLE", environment_payload["status"])
            self.assertEqual("1.26.3", environment_payload["compiler_version"])

            command = (
                "modelica", "compile", str(MODEL), "--model", "FarcelFirstOrder",
                "--output-dir", str(output), "--omc", str(compiler_path), "--json",
            )
            compiled = _run(cli, *command)
            self.assertEqual(0, compiled.returncode, compiled.stderr)
            payload = json.loads(compiled.stdout)
            self.assertTrue(payload["success"])
            self.assertEqual("2.0", payload["fmi_version"])
            self.assertEqual("cs", payload["fmu_type"])
            fmu = Path(payload["fmu_path"])
            self.assertTrue(fmu.is_file())
            self.assertEqual(_sha256(fmu), payload["sha256"])

            inspected = _run(cli, "inspect", str(fmu), "--json")
            self.assertEqual(0, inspected.returncode, inspected.stderr)
            self.assertEqual("2.0", json.loads(inspected.stdout)["fmi_version"])
            executed = _run(
                cli, "run", str(fmu), "--start-time", "0", "--stop-time", "2",
                "--step-size", "0.01", "--parameter", "T=0.5", "--input", "u=1",
                "--output", "y",
            )
            self.assertEqual(0, executed.returncode, executed.stderr)
            self.assertIn("execution successful: yes", executed.stdout)

            result = create_backend().run_fmu(
                fmu,
                SimulationConfig(
                    start_time=0.0,
                    stop_time=2.0,
                    communication_step=0.01,
                    parameters={"T": 0.5},
                    initial_inputs={"u": 1.0},
                    selected_outputs=("y",),
                ),
            )
            self.assertIs(SimulationState.COMPLETED, result.completion_state)
            self.assertLessEqual(
                max(
                    abs(float(value) - (1.0 - math.exp(-time / 0.5)))
                    for time, value in zip(result.timestamps, result.outputs["y"])
                ),
                0.03,
            )

            original_sha256 = _sha256(fmu)
            conflict = _run(cli, *command)
            self.assertEqual(1, conflict.returncode)
            conflict_payload = json.loads(conflict.stderr)
            self.assertFalse(conflict_payload["success"])
            self.assertEqual("MODELICA_OUTPUT_CONFLICT", conflict_payload["error"]["code"])
            self.assertEqual(original_sha256, _sha256(fmu))


def _run(cli: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [cli, *arguments],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding=locale.getpreferredencoding(False),
        check=False,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
