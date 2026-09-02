from __future__ import annotations

import unittest
from pathlib import Path

from farcel.contracts.models import InterfaceType, SimulationConfig
from farcel.infrastructure.fmpy import (
    FmpyFmi2ModelExchangeSessionFactory,
    FmpyImporter,
)


FMU_FIXTURES = Path(__file__).resolve().parents[2] / "examples" / "fmus"


class Fmi2ModelExchangeSessionIntegrationTests(unittest.TestCase):
    van_der_pol = FMU_FIXTURES / "VanDerPol.fmu"

    @unittest.skipUnless(van_der_pol.is_file(), "VanDerPol FMU is unavailable")
    def test_van_der_pol_initializes_and_exposes_continuous_problem_primitives(self) -> None:
        metadata = FmpyImporter().load(self.van_der_pol)
        self.assertEqual(metadata.fmi_version, "2.0")
        self.assertIn(InterfaceType.MODEL_EXCHANGE, metadata.interface_types)
        self.assertIs(metadata.executable_interface, InterfaceType.CO_SIMULATION)

        session = FmpyFmi2ModelExchangeSessionFactory().create(
            metadata, SimulationConfig(selected_outputs=("x0",))
        )
        extraction = session._extraction_directory
        try:
            initialization = session.initialize()
            self.assertGreater(initialization.continuous_state_count, 0)
            self.assertEqual(len(session.get_continuous_states()), 2)
            self.assertEqual(len(session.get_derivatives()), 2)
            self.assertEqual(session.get_event_indicators(), ())
            session.set_time(0.0)
            session.set_continuous_states(session.get_continuous_states())
            session.completed_integrator_step()
            self.assertIn("x0", session.read_outputs())
            session.terminate()
        finally:
            session.close()
        self.assertFalse(extraction.exists())
