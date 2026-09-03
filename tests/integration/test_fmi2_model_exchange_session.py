from __future__ import annotations

import unittest
from pathlib import Path

from farcel import create_backend
from farcel.contracts.models import InterfaceType, SimulationConfig
from farcel.application.model_exchange_problem import SessionModelExchangeProblem
from farcel.application.model_exchange_runtime import ModelExchangeCheckpointCoordinator
from farcel.application.runners import ModelExchangeRunner
from farcel.contracts.models import SolverAdvanceStatus, SolverOptions
from farcel.infrastructure.fmpy import (
    FmpyCvodeSolverFactory,
    FmpyFmi2ModelExchangeSessionFactory,
    FmpyImporter,
)


FMU_FIXTURES = Path(__file__).resolve().parents[2] / "examples" / "fmus"


class Fmi2ModelExchangeSessionIntegrationTests(unittest.TestCase):
    van_der_pol = FMU_FIXTURES / "VanDerPol.fmu"
    stair = FMU_FIXTURES / "Stair.fmu"

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

    @unittest.skipUnless(stair.is_file(), "Stair FMU is unavailable")
    def test_stair_time_event_advances_through_event_mode(self) -> None:
        config = SimulationConfig(start_time=0, stop_time=1.2, communication_step=.2, selected_outputs=("counter",))
        metadata = FmpyImporter().load(self.stair)
        capability = next(item for item in metadata.interface_capabilities if item.interface_type is InterfaceType.MODEL_EXCHANGE)
        self.assertTrue(capability.needs_completed_integrator_step)
        session = FmpyFmi2ModelExchangeSessionFactory().create(metadata, config)
        solver = FmpyCvodeSolverFactory().create()
        try:
            initialization = session.initialize()
            self.assertEqual(initialization.next_event_time, 1.0)
            solver.initialize(SessionModelExchangeProblem(session), SolverOptions(relative_tolerance=1e-5))
            coordinator = ModelExchangeCheckpointCoordinator(session, solver, config, initialization, needs_completed_integrator_step=True)
            for checkpoint in (.2, .4, .6, .8): self.assertTrue(coordinator.advance_to(checkpoint).checkpoint_reached)
            event = coordinator.advance_to(1.0)
            self.assertTrue(event.checkpoint_reached)
            self.assertEqual(event.event_count, 1)
            self.assertEqual(session.read_outputs()["counter"], 2)
            after_event = coordinator.advance_to(1.2)
            self.assertTrue(after_event.checkpoint_reached)
            self.assertAlmostEqual(coordinator.current_time, 1.2, places=12)
            self.assertEqual(session.read_outputs()["counter"], 2)
        finally:
            solver.close(); session.terminate(); session.close()

    @unittest.skipUnless(van_der_pol.is_file(), "VanDerPol FMU is unavailable")
    def test_van_der_pol_model_exchange_runner_returns_canonical_result_and_matches_cs(self) -> None:
        config = SimulationConfig(
            start_time=0.0, stop_time=0.05, communication_step=0.01,
            parameters={"mu": 2.0}, selected_outputs=("x0",),
        )
        metadata = FmpyImporter().load(self.van_der_pol)
        result = ModelExchangeRunner(
            FmpyFmi2ModelExchangeSessionFactory(), FmpyCvodeSolverFactory()
        ).run(self.van_der_pol, metadata, config)
        cs_result = create_backend().run_fmu(self.van_der_pol, config)
        self.assertIs(result.completion_state, result.completion_state.COMPLETED)
        self.assertEqual(result.completed_steps, 5)
        self.assertEqual(result.timestamps, (0.0, 0.01, 0.02, 0.03, 0.04, 0.05))
        self.assertAlmostEqual(result.final_time, 0.05, places=12)
        self.assertIn("x0", result.outputs)
        self.assertAlmostEqual(result.outputs["x0"][-1], cs_result.outputs["x0"][-1], places=3)

    @unittest.skipUnless(stair.is_file(), "Stair FMU is unavailable")
    def test_stair_model_exchange_runner_crosses_time_event_and_continues(self) -> None:
        config = SimulationConfig(
            start_time=0.0, stop_time=1.2, communication_step=0.2,
            selected_outputs=("counter",),
        )
        metadata = FmpyImporter().load(self.stair)
        result = ModelExchangeRunner(
            FmpyFmi2ModelExchangeSessionFactory(), FmpyCvodeSolverFactory()
        ).run(self.stair, metadata, config)
        self.assertIs(result.completion_state, result.completion_state.COMPLETED)
        self.assertEqual(result.completed_steps, 6)
        self.assertAlmostEqual(result.final_time, 1.2, places=12)
        self.assertEqual(result.outputs["counter"][-1], 2)
