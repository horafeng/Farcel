from __future__ import annotations

import unittest
from pathlib import Path

from farcel import create_backend
from farcel.contracts.models import InterfaceType, SimulationConfig
from farcel.application.model_exchange_problem import SessionModelExchangeProblem
from farcel.contracts.models import SolverAdvanceStatus, SolverOptions
from farcel.infrastructure.fmpy import (
    FmpyCvodeSolverFactory,
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

    @unittest.skipUnless(van_der_pol.is_file(), "VanDerPol FMU is unavailable")
    def test_van_der_pol_cvode_advances_multiple_checkpoints_and_matches_cs_baseline(self) -> None:
        config = SimulationConfig(
            start_time=0.0, stop_time=0.05, communication_step=0.01,
            parameters={"mu": 2.0}, selected_outputs=("x0",),
        )
        metadata = FmpyImporter().load(self.van_der_pol)
        session = FmpyFmi2ModelExchangeSessionFactory().create(metadata, config)
        solver = FmpyCvodeSolverFactory().create()
        extraction = session._extraction_directory
        try:
            session.initialize()
            problem = SessionModelExchangeProblem(session)
            self.assertEqual(problem.get_initial_time(), config.start_time)
            self.assertEqual(len(problem.get_nominals()), 2)
            solver.initialize(problem, SolverOptions(relative_tolerance=1e-5))
            reached = []
            for target in (0.01, 0.02, 0.03, 0.04, 0.05):
                result = solver.integrate_to(target)
                self.assertIs(result.status, SolverAdvanceStatus.REACHED_TARGET)
                self.assertAlmostEqual(result.reached_time, target, places=12)
                reached.append(session.read_outputs()["x0"])
            # CS and ME use different internal solvers, so this is numerical,
            # not bit-identical, agreement for the same FMI model.
            cs_result = create_backend().run_fmu(self.van_der_pol, config)
            self.assertAlmostEqual(reached[-1], cs_result.outputs["x0"][-1], places=3)
        finally:
            solver.close()
            session.terminate()
            session.close()
        self.assertFalse(extraction.exists())
