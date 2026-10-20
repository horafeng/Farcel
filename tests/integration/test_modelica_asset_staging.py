"""Opt-in real OpenModelica coverage for unregistered project asset staging."""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from farcel import create_backend
from farcel.application.modelica_asset_staging import ModelicaAssetStagingService
from farcel.application.project_assets import ProjectAssetValidator
from farcel.contracts import ModelicaAssetStagingRequest, SimulationConfig, SimulationState
from farcel.infrastructure.openmodelica import OpenModelicaCompiler
from farcel.infrastructure.project import LocalModelicaAssetStore


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MODEL = REPOSITORY_ROOT / "examples" / "modelica" / "FarcelFirstOrder.mo"
DEFAULT_OMC = Path(r"D:\OpenModelica\bin\omc.exe")


@unittest.skipUnless(
    os.environ.get("FARCEL_RUN_OPENMODELICA_INTEGRATION") == "1",
    "需要显式设置 FARCEL_RUN_OPENMODELICA_INTEGRATION=1 并安装 OpenModelica",
)
class ModelicaAssetStagingIntegrationTests(unittest.TestCase):
    def test_real_snapshot_compile_stage_relocation_and_run(self) -> None:
        compiler_path = Path(os.environ.get("FARCEL_OPENMODELICA_OMC", DEFAULT_OMC))
        self.assertTrue(compiler_path.is_file(), f"未找到 OMC: {compiler_path}")

        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT / "artifacts") as directory:
            root = Path(directory) / "Project"
            root.mkdir()
            project_json = root / "project.json"
            project_bytes = b'{"schema_version":"1.0"}\n'
            project_json.write_bytes(project_bytes)
            service = ModelicaAssetStagingService(
                OpenModelicaCompiler(),
                LocalModelicaAssetStore(),
            )
            staged = service.stage(
                ModelicaAssetStagingRequest(
                    project_root=root,
                    source_path=MODEL,
                    model_name="FarcelFirstOrder",
                    compiler_path=compiler_path,
                )
            )

            fmu_path = root / staged.model_asset.relative_path
            source_path = root / staged.provenance.source_snapshot.relative_path
            build_directory = fmu_path.parent.parent
            self.assertEqual(project_bytes, project_json.read_bytes())
            self.assertEqual(
                {".farcel-modelica-staging", "source", "model"},
                {entry.name for entry in build_directory.iterdir()},
            )
            self.assertFalse((build_directory / "compiler-output").exists())
            self.assertTrue((build_directory / ".farcel-modelica-staging").is_file())
            self.assertEqual(_sha256(source_path), staged.provenance.source_snapshot.sha256)
            self.assertEqual(_sha256(fmu_path), staged.model_asset.sha256)
            self.assertEqual(staged.model_asset.asset_id, staged.provenance.fmu_asset_id)
            self.assertEqual("OpenModelica", staged.provenance.compiler_name)
            self.assertEqual("1.26.3", staged.provenance.compiler_version)

            backend = create_backend()
            metadata = backend.load_fmu(fmu_path)
            result = backend.run_fmu(
                fmu_path,
                SimulationConfig(
                    start_time=0.0,
                    stop_time=2.0,
                    communication_step=0.01,
                    parameters={"T": 0.5},
                    initial_inputs={"u": 1.0},
                    selected_outputs=("y",),
                ),
            )
            self.assertEqual("2.0", metadata.fmi_version)
            self.assertIs(SimulationState.COMPLETED, result.completion_state)
            self.assertLessEqual(
                max(
                    abs(float(value) - (1.0 - math.exp(-time / 0.5)))
                    for time, value in zip(result.timestamps, result.outputs["y"])
                ),
                0.03,
            )

            relocated = Path(directory) / "RelocatedProject"
            shutil.copytree(root, relocated)
            relocated_source = relocated / staged.provenance.source_snapshot.relative_path
            self.assertEqual(_sha256(relocated_source), staged.provenance.source_snapshot.sha256)
            self.assertTrue(
                ProjectAssetValidator().check(relocated, staged.model_asset).report.is_valid
            )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
