import unittest
from dataclasses import FrozenInstanceError

from farcel.contracts import (
    DiscreteStateUpdate,
    IntegratorStepResult,
    ModelExchangeInitialization,
    ModelExchangeProblem,
    ModelExchangeSession,
    ModelExchangeSessionFactory,
    SolverAdapter,
    SolverAdvanceResult,
    SolverAdvanceStatus,
    SolverFactory,
    SolverOptions,
    SolverResetReason,
)


class ModelExchangeContractTests(unittest.TestCase):
    def test_model_exchange_dtos_have_immutable_minimal_defaults(self) -> None:
        initialization = ModelExchangeInitialization(2, 1)
        update = DiscreteStateUpdate(discrete_states_need_update=True)
        integrator_step = IntegratorStepResult()

        self.assertEqual(initialization.continuous_state_count, 2)
        self.assertEqual(initialization.event_indicator_count, 1)
        self.assertFalse(initialization.next_event_time_defined)
        self.assertIsNone(initialization.next_event_time)
        self.assertTrue(update.discrete_states_need_update)
        self.assertFalse(update.terminate_requested)
        self.assertFalse(integrator_step.enter_event_mode)
        with self.assertRaises(FrozenInstanceError):
            initialization.terminate_requested = True

    def test_solver_dtos_express_event_and_failure_without_native_types(self) -> None:
        event = SolverAdvanceResult(
            reached_time=0.2,
            status=SolverAdvanceStatus.STATE_EVENT,
            root_info=(1, -1),
        )
        failure = SolverAdvanceResult(
            reached_time=0.2,
            status=SolverAdvanceStatus.FAILED,
            failure_message="solver failed",
        )
        options = SolverOptions(relative_tolerance=1e-6)

        self.assertEqual(event.root_info, (1, -1))
        self.assertEqual(failure.failure_message, "solver failed")
        self.assertIsNone(options.maximum_step)
        self.assertEqual(
            SolverResetReason.CONTINUOUS_STATES_CHANGED.value,
            "continuous_states_changed",
        )
        with self.assertRaises(FrozenInstanceError):
            options.maximum_step = 0.01

    def test_model_exchange_and_solver_protocols_freeze_required_operations(self) -> None:
        self.assertTrue(
            {
                "initialize",
                "get_initial_time",
                "get_event_indicator_count",
                "set_inputs",
                "set_time",
                "get_continuous_states",
                "get_nominals_of_continuous_states",
                "set_continuous_states",
                "get_derivatives",
                "get_event_indicators",
                "completed_integrator_step",
                "enter_event_mode",
                "update_discrete_states",
                "enter_continuous_time_mode",
                "read_outputs",
                "terminate",
                "close",
            }.issubset(ModelExchangeSession.__dict__)
        )
        self.assertIn("create", ModelExchangeSessionFactory.__dict__)
        self.assertTrue(
            {
                "get_initial_states",
                "get_initial_time",
                "get_nominals",
                "get_event_indicator_count",
                "set_state",
                "get_derivatives",
                "get_event_indicators",
            }.issubset(ModelExchangeProblem.__dict__)
        )
        self.assertTrue(
            {"initialize", "integrate_to", "reset", "close"}.issubset(
                SolverAdapter.__dict__
            )
        )
        self.assertIn("create", SolverFactory.__dict__)
