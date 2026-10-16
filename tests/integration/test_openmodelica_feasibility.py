from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MODEL = REPOSITORY_ROOT / "examples" / "modelica" / "FarcelFirstOrder.mo"
SCRIPT = REPOSITORY_ROOT / "tools" / "modelica" / "validate_openmodelica_fmu.py"


def _load_probe_module():
    spec = spec_from_file_location("openmodelica_probe", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 OpenModelica 预验证工具")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class OpenModelicaFeasibilityTests(unittest.TestCase):
    def test_probe_model_has_the_required_minimal_interface(self) -> None:
        source = MODEL.read_text(encoding="utf-8")

        self.assertIn("model FarcelFirstOrder", source)
        self.assertIn("parameter Real T", source)
        self.assertIn("input Real u", source)
        self.assertIn("output Real y", source)

    def test_probe_reports_missing_omc_without_claiming_success(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--omc",
                str(REPOSITORY_ROOT / "missing-omc.exe"),
                "--work-dir",
                str(REPOSITORY_ROOT / "artifacts" / "modelica-test-missing"),
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(2, completed.returncode)
        self.assertIn("未找到 OpenModelica OMC", completed.stderr)

    def test_probe_returns_nonzero_when_omc_compilation_fails(self) -> None:
        probe = _load_probe_module()
        with (
            patch.object(probe, "discover_omc", return_value=Path("omc")),
            patch.object(probe, "_write_omc_script", return_value=SCRIPT),
            patch.object(
                probe.subprocess,
                "run",
                side_effect=(
                    subprocess.CompletedProcess([], 0, "OpenModelica fake", ""),
                    subprocess.CompletedProcess([], 13, "", "fake compiler failure"),
                ),
            ),
        ):
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                exit_code = probe.main(
                    ["--omc", "omc", "--work-dir", str(REPOSITORY_ROOT)]
                )

        self.assertEqual(4, exit_code)

    @unittest.skipUnless(
        os.environ.get("FARCEL_RUN_OPENMODELICA_INTEGRATION") == "1",
        "需要显式设置 FARCEL_RUN_OPENMODELICA_INTEGRATION=1 并安装 OpenModelica",
    )
    def test_real_omc_fmu_compiles_and_runs_through_public_farcel_api(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT / "artifacts") as directory:
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--work-dir", directory],
                cwd=REPOSITORY_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
