"""Opt-in end-to-end coverage for the installed Modelica Project CLI."""

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
from farcel.contracts import SimulationConfig, SimulationProject, SimulationState


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MODEL = REPOSITORY_ROOT / "examples" / "modelica" / "FarcelFirstOrder.mo"
DEFAULT_OMC = Path(r"D:\OpenModelica\bin\omc.exe")


@unittest.skipUnless(
    os.environ.get("FARCEL_RUN_OPENMODELICA_INTEGRATION") == "1",
    "需要显式设置 FARCEL_RUN_OPENMODELICA_INTEGRATION=1 并安装 OpenModelica",
)
class ModelicaProjectCliRealIntegrationTests(unittest.TestCase):
    def test_installed_cli_migrates_compiles_lists_and_relocates(self) -> None:
        cli = shutil.which("farcel")
        self.assertIsNotNone(cli, "未找到已安装的 farcel CLI 入口")
        compiler_path = Path(os.environ.get("FARCEL_OPENMODELICA_OMC", DEFAULT_OMC))
        self.assertTrue(compiler_path.is_file(), f"未找到 OMC: {compiler_path}")

        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT / "artifacts") as directory:
            root = Path(directory) / "Project with 空格"
            root.mkdir()
            backend = create_backend()
            legacy = SimulationProject("project-001", "真实 Project CLI 项目")
            backend.save_project(root, legacy)

            rejected = _run(
                cli, "modelica", "project", "compile", "--project-root", str(root),
                "--source", str(MODEL), "--model", "FarcelFirstOrder", "--json",
            )
            self.assertEqual(1, rejected.returncode)
            self.assertEqual("CONFIG_ERROR", json.loads(rejected.stderr)["error"]["code"])
            self.assertEqual("1.0", backend.open_project(root).schema_version)

            migrated = _run(
                cli, "modelica", "project", "migrate", "--project-root", str(root), "--json"
            )
            self.assertEqual(0, migrated.returncode, migrated.stderr)
            migration_payload = json.loads(migrated.stdout)
            self.assertTrue(migration_payload["success"])
            self.assertEqual("1.1", migration_payload["schema_version"])
            self.assertTrue((root / migration_payload["backup_relative_path"]).is_file())
            project = backend.open_project(root)
            self.assertEqual("1.1", project.schema_version)
            self.assertEqual((), project.simulation_cases)
            self.assertEqual((), project.run_history)

            repeated_migration = _run(
                cli, "modelica", "project", "migrate", "--project-root", str(root), "--json"
            )
            self.assertEqual(1, repeated_migration.returncode)
            self.assertEqual(
                "PROJECT_FORMAT_ERROR",
                json.loads(repeated_migration.stderr)["error"]["code"],
            )

            compiled = _run(
                cli, "modelica", "project", "compile", "--project-root", str(root),
                "--source", str(MODEL), "--model", "FarcelFirstOrder",
                "--omc", str(compiler_path), "--json",
            )
            self.assertEqual(0, compiled.returncode, compiled.stderr)
            compilation_payload = json.loads(compiled.stdout)
            self.assertTrue(compilation_payload["success"])
            self.assertEqual("1.1", compilation_payload["schema_version"])
            self.assertEqual("FarcelFirstOrder", compilation_payload["model_name"])

            reopened = backend.open_project(root)
            self.assertEqual(1, len(reopened.model_assets))
            self.assertEqual(1, len(reopened.modelica_build_provenance))
            self.assertEqual((), reopened.simulation_cases)
            self.assertEqual((), reopened.run_history)
            provenance = reopened.modelica_build_provenance[0]
            asset = reopened.model_assets[0]
            self.assertEqual(compilation_payload["build_id"], provenance.build_id)
            self.assertEqual(compilation_payload["asset_id"], asset.asset_id)
            self.assertEqual(_sha256(root / provenance.source_snapshot.relative_path), provenance.source_snapshot.sha256)
            fmu = root / asset.relative_path
            self.assertEqual(_sha256(fmu), asset.sha256)
            self.assertTrue(backend.validate_project(root, reopened).is_valid)

            result = backend.run_fmu(
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

            listed = _run(cli, "modelica", "project", "list", "--project-root", str(root), "--json")
            self.assertEqual(0, listed.returncode, listed.stderr)
            list_payload = json.loads(listed.stdout)
            self.assertEqual(1, list_payload["count"])
            self.assertEqual(asset.relative_path, list_payload["builds"][0]["fmu_relative_path"])
            self.assertEqual(asset.sha256, list_payload["builds"][0]["fmu_sha256"])

            relocated = Path(directory) / "Relocated Project"
            shutil.copytree(root, relocated)
            relocated_list = _run(
                cli, "modelica", "project", "list", "--project-root", str(relocated), "--json"
            )
            self.assertEqual(0, relocated_list.returncode, relocated_list.stderr)
            self.assertEqual(1, json.loads(relocated_list.stdout)["count"])
            self.assertTrue(backend.validate_project(relocated, backend.open_project(relocated)).is_valid)

            missing = _run(cli, "modelica", "project", "list", "--project-root", str(root / "missing"), "--json")
            self.assertEqual(1, missing.returncode)
            self.assertEqual("PROJECT_IO_ERROR", json.loads(missing.stderr)["error"]["code"])


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
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
