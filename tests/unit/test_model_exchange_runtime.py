from __future__ import annotations

import unittest
from unittest.mock import patch

from farcel.application.model_exchange_runtime import ModelExchangeCheckpointCoordinator
from farcel.contracts import (
    DiscreteStateUpdate, ErrorCode, InputUpdate, IntegratorStepResult,
    ModelExchangeInitialization, SimulationConfig, SolverAdvanceResult,
    SolverAdvanceStatus, SolverResetReason,
)


class _Session:
    def __init__(self, updates=()):
        self.updates = list(updates); self.events = []; self.inputs = []
    def set_inputs(self, values): self.inputs.append(values)
    def completed_integrator_step(self): self.events.append("completed"); return IntegratorStepResult()
    def enter_event_mode(self): self.events.append("event")
    def update_discrete_states(self): self.events.append("update"); return self.updates.pop(0) if self.updates else DiscreteStateUpdate(False)
    def enter_continuous_time_mode(self): self.events.append("continuous")


class _Solver:
    def __init__(self, results): self.results = list(results); self.targets = []; self.resets = []
    def integrate_to(self, target): self.targets.append(target); return self.results.pop(0)
    def reset(self, time, reason): self.resets.append((time, reason))


class ModelExchangeRuntimeTests(unittest.TestCase):
    def _coordinator(self, session, solver, *, schedule=(), event_time=None, completed=False):
        init = ModelExchangeInitialization(1, 0, next_event_time_defined=event_time is not None, next_event_time=event_time)
        return ModelExchangeCheckpointCoordinator(session, solver, SimulationConfig(start_time=0, stop_time=1, communication_step=.1, input_schedule=schedule), init, needs_completed_integrator_step=completed)

    def test_state_event_continues_to_original_checkpoint_and_resets_once(self):
        session = _Session([DiscreteStateUpdate(False, continuous_states_changed=True)])
        solver = _Solver([SolverAdvanceResult(.06, SolverAdvanceStatus.STATE_EVENT, (1,)), SolverAdvanceResult(.1, SolverAdvanceStatus.REACHED_TARGET)])
        outcome = self._coordinator(session, solver).advance_to(.1)
        self.assertTrue(outcome.checkpoint_reached); self.assertEqual(solver.targets, [.1, .1])
        self.assertEqual(solver.resets, [(.06, SolverResetReason.CONTINUOUS_STATES_CHANGED)])

    def test_pure_time_event_does_not_reset_issue_882_regression(self):
        session = _Session([DiscreteStateUpdate(False)])
        solver = _Solver([SolverAdvanceResult(.1, SolverAdvanceStatus.REACHED_TARGET)])
        outcome = self._coordinator(session, solver, event_time=0).advance_to(.1)
        self.assertTrue(outcome.checkpoint_reached); self.assertEqual(solver.resets, [])
        self.assertEqual(solver.targets, [.1])

    def test_input_and_time_event_share_one_cycle_and_nominal_precedence(self):
        session = _Session([DiscreteStateUpdate(False, continuous_states_changed=True, nominals_changed=True)])
        solver = _Solver([SolverAdvanceResult(.1, SolverAdvanceStatus.REACHED_TARGET)])
        schedule = (InputUpdate(0, {"u": 2.0}),)
        self._coordinator(session, solver, schedule=schedule, event_time=0).advance_to(.1)
        self.assertEqual(session.inputs, [{"u": 2.0}]); self.assertEqual(session.events.count("event"), 1)
        self.assertEqual(solver.resets, [(0, SolverResetReason.NOMINALS_CHANGED)])

    def test_completed_integrator_step_is_capability_gated(self):
        session = _Session(); solver = _Solver([SolverAdvanceResult(.1, SolverAdvanceStatus.REACHED_TARGET)])
        self._coordinator(session, solver, completed=False).advance_to(.1); self.assertNotIn("completed", session.events)
        session = _Session(); solver = _Solver([SolverAdvanceResult(.1, SolverAdvanceStatus.REACHED_TARGET)])
        self._coordinator(session, solver, completed=True).advance_to(.1); self.assertEqual(session.events, ["completed"])

    def test_discrete_updates_aggregate_changes_and_terminate_stays_in_event_mode(self):
        session = _Session([DiscreteStateUpdate(True, continuous_states_changed=True), DiscreteStateUpdate(False, nominals_changed=True)])
        solver = _Solver([SolverAdvanceResult(.1, SolverAdvanceStatus.STATE_EVENT)])
        self._coordinator(session, solver).advance_to(.1)
        self.assertEqual(solver.resets, [(.1, SolverResetReason.NOMINALS_CHANGED)])
        session = _Session([DiscreteStateUpdate(False, terminate_requested=True)])
        solver = _Solver([SolverAdvanceResult(.1, SolverAdvanceStatus.STATE_EVENT)])
        outcome = self._coordinator(session, solver).advance_to(.1)
        self.assertTrue(outcome.terminate_requested); self.assertNotIn("continuous", session.events)

    def test_no_progress_without_event_is_a_stable_step_error(self):
        session = _Session()
        solver = _Solver([SolverAdvanceResult(0.0, SolverAdvanceStatus.REACHED_TARGET)])
        with self.assertRaisesRegex(Exception, "solver 未取得进展") as raised:
            self._coordinator(session, solver).advance_to(.1)
        self.assertIs(raised.exception.code, ErrorCode.STEP_ERROR)

    def test_repeated_same_time_event_hits_bounded_event_cycle_guard(self):
        session = _Session([
            DiscreteStateUpdate(False, next_event_time_defined=True, next_event_time=0.0),
            DiscreteStateUpdate(False, next_event_time_defined=True, next_event_time=0.0),
        ])
        solver = _Solver([])
        with patch("farcel.application.model_exchange_runtime._MAX_EVENT_CYCLES_PER_CHECKPOINT", 2):
            with self.assertRaisesRegex(Exception, "事件循环超过上限") as raised:
                self._coordinator(session, solver, event_time=0).advance_to(.1)
        self.assertIs(raised.exception.code, ErrorCode.STEP_ERROR)

    def test_unstable_discrete_updates_hit_iteration_guard(self):
        session = _Session([DiscreteStateUpdate(True), DiscreteStateUpdate(True)])
        solver = _Solver([SolverAdvanceResult(.05, SolverAdvanceStatus.STATE_EVENT)])
        with patch("farcel.application.model_exchange_runtime._MAX_ME_DISCRETE_ITERATIONS", 2):
            with self.assertRaisesRegex(Exception, "离散状态迭代超过上限") as raised:
                self._coordinator(session, solver).advance_to(.1)
        self.assertIs(raised.exception.code, ErrorCode.STEP_ERROR)
