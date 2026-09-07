from __future__ import annotations

import unittest
from pathlib import Path

from farcel.application.node_runtime import ModelExchangeNodeRuntimeFactory
from farcel.contracts import InterfaceType, SimulationConfig
from farcel.infrastructure.fmpy import (
    FmpyCvodeSolverFactory,
    FmpyFmi2ModelExchangeSessionFactory,
    FmpyImporter,
)


FMU_FIXTURES = Path(__file__).resolve().parents[2] / "examples" / "fmus"


class ModelExchangeNodeRuntimeIntegrationTests(unittest.TestCase):
    van_der_pol = FMU_FIXTURES / "VanDerPol.fmu"
    feedthrough = FMU_FIXTURES / "Feedthrough-fmi2.fmu"

    @staticmethod
    def _factory() -> ModelExchangeNodeRuntimeFactory:
        return ModelExchangeNodeRuntimeFactory(
            FmpyFmi2ModelExchangeSessionFactory(), FmpyCvodeSolverFactory()
        )

    @unittest.skipUnless(van_der_pol.is_file(), "VanDerPol FMU is unavailable")
    def test_van_der_pol_reaches_two_checkpoints(self) -> None:
        config = SimulationConfig(
            stop_time=0.02,
            communication_step=0.01,
            selected_outputs=("x0",),
            execution_interface=InterfaceType.MODEL_EXCHANGE,
        )
        runtime = self._factory().create(FmpyImporter().load(self.van_der_pol), config)
        try:
            runtime.initialize()
            initial = runtime.read_outputs()["x0"]
            runtime.advance_to(0.01)
            runtime.advance_to(0.02)
            final = runtime.read_outputs()["x0"]
            self.assertAlmostEqual(initial, 2.0, places=8)
            self.assertNotEqual(initial, final)
            runtime.terminate()
        finally:
            runtime.close()

    @unittest.skipUnless(feedthrough.is_file(), "FMI 2 Feedthrough FMU is unavailable")
    def test_feedthrough_routed_input_event_is_visible_after_checkpoint(self) -> None:
        config = SimulationConfig(
            stop_time=0.02,
            communication_step=0.01,
            initial_inputs={"Float64_continuous_input": 1.0},
            selected_outputs=("Float64_continuous_output",),
            execution_interface=InterfaceType.MODEL_EXCHANGE,
        )
        runtime = self._factory().create(FmpyImporter().load(self.feedthrough), config)
        try:
            runtime.initialize()
            self.assertAlmostEqual(
                runtime.read_outputs()["Float64_continuous_output"], 1.0, places=8
            )
            runtime.set_inputs({"Float64_continuous_input": 4.0})
            runtime.advance_to(0.01)
            self.assertAlmostEqual(
                runtime.read_outputs()["Float64_continuous_output"], 4.0, places=8
            )
            runtime.advance_to(0.02)
            runtime.terminate()
        finally:
            runtime.close()
