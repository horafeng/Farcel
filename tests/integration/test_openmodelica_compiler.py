"""Opt-in end-to-end coverage for the OpenModelica compiler adapter."""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import tempfile
import unittest

from farcel import create_backend
from farcel.contracts import (
    InterfaceType,
    ModelicaCompileRequest,
    SimulationConfig,
    SimulationState,
)
from farcel.infrastructure.openmodelica import OpenModelicaCompiler


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MODEL = REPOSITORY_ROOT / "examples" / "modelica" / "FarcelFirstOrder.mo"
DEFAULT_OMC = Path(r"D:\OpenModelica\bin\omc.exe")


@unittest.skipUnless(
    os.environ.get("FARCEL_RUN_OPENMODELICA_INTEGRATION") == "1",
    "需要显式设置 FARCEL_RUN_OPENMODELICA_INTEGRATION=1 并安装 OpenModelica",
)
class OpenModelicaCompilerIntegrationTests(unittest.TestCase):
    def test_real_omc_compiles_and_farcel_runs_the_result(self) -> None:
        configured_omc = Path(os.environ.get("FARCEL_OPENMODELICA_OMC", DEFAULT_OMC))
        self.assertTrue(configured_omc.is_file(), f"未找到 OMC: {configured_omc}")

        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT / "artifacts") as directory:
            output_directory = Path(directory) / "output"
            output_directory.mkdir()
            result = OpenModelicaCompiler().compile(
                ModelicaCompileRequest(
                    source_path=MODEL,
                    model_name="FarcelFirstOrder",
                    output_directory=output_directory,
                    compiler_path=configured_omc,
                )
            )

            self.assertTrue(result.fmu_path.is_file())
            self.assertEqual("2.0", result.fmi_version)
            self.assertEqual("cs", result.fmu_type)
            self.assertEqual("1.26.3", result.compiler_version)
            self.assertEqual(_sha256(result.fmu_path), result.sha256)

            backend = create_backend()
            metadata = backend.load_fmu(result.fmu_path)
            self.assertEqual("2.0", metadata.fmi_version)
            self.assertIs(InterfaceType.CO_SIMULATION, metadata.executable_interface)

            simulation = backend.run_fmu(
                result.fmu_path,
                SimulationConfig(
                    start_time=0.0,
                    stop_time=2.0,
                    communication_step=0.01,
                    parameters={"T": 0.5},
                    initial_inputs={"u": 1.0},
                    selected_outputs=("y",),
                ),
            )
            self.assertIs(SimulationState.COMPLETED, simulation.completion_state)
            actual = [float(value) for value in simulation.outputs["y"]]
            expected = [1.0 - math.exp(-time / 0.5) for time in simulation.timestamps]
            self.assertLessEqual(
                max(abs(value - target) for value, target in zip(actual, expected)),
                0.03,
            )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    unittest.main()
