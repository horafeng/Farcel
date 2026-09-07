from __future__ import annotations

import unittest
from pathlib import Path

from farcel.application.node_runtime import CoSimulationNodeRuntimeFactory
from farcel.contracts import InterfaceType, SimulationConfig
from farcel.infrastructure.fmpy import FmpyImporter, FmpySessionFactory


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FMU_DIRECTORY = REPOSITORY_ROOT / "examples" / "fmus"


class CoSimulationNodeRuntimeIntegrationTests(unittest.TestCase):
    def test_fmi2_and_fmi3_nodes_advance_to_two_checkpoints(self) -> None:
        for filename in ("VanDerPol.fmu", "VanDerPol-fmi3.fmu"):
            with self.subTest(filename=filename):
                metadata = FmpyImporter().load(FMU_DIRECTORY / filename)
                config = SimulationConfig(
                    stop_time=0.02,
                    communication_step=0.01,
                    selected_outputs=("x0",),
                    execution_interface=InterfaceType.CO_SIMULATION,
                )
                runtime = CoSimulationNodeRuntimeFactory(FmpySessionFactory()).create(
                    metadata, config
                )
                try:
                    runtime.initialize()
                    self.assertIn("x0", runtime.read_outputs())
                    runtime.advance_to(0.01)
                    runtime.advance_to(0.02)
                    self.assertIn("x0", runtime.read_outputs())
                    runtime.terminate()
                finally:
                    runtime.close()
