from __future__ import annotations

import unittest
from unittest.mock import patch

from farcel.contracts import SolverAdvanceStatus, SolverOptions, SolverResetReason
from farcel.infrastructure.fmpy.cvode_solver import FmpyCvodeSolverAdapter


class CvodeSolverCharacterizationTests(unittest.TestCase):
    @staticmethod
    def _problem():
        class Problem:
            def get_initial_time(self): return 0.0
            def get_initial_states(self): return (1.0,)
            def get_nominals(self): return (1.0,)
            def get_event_indicator_count(self): return 0
            def set_state(self, time, states): self.time, self.states = time, states
            def get_derivatives(self): return (0.0,)
            def get_event_indicators(self): return ()

        return Problem()

    def test_same_time_and_backward_targets_do_not_enter_native_solver(self) -> None:
        solver = FmpyCvodeSolverAdapter()
        solver._problem = object()
        solver._time = 1.0
        same = solver.integrate_to(1.0)
        backward = solver.integrate_to(.5)
        self.assertIs(same.status, SolverAdvanceStatus.REACHED_TARGET)
        self.assertIs(backward.status, SolverAdvanceStatus.FAILED)

    def test_close_is_idempotent_before_initialization(self) -> None:
        solver = FmpyCvodeSolverAdapter()
        solver.close()
        solver.close()
        self.assertTrue(solver._closed)

    def test_initialized_close_is_idempotent(self) -> None:
        solver = FmpyCvodeSolverAdapter()
        solver.initialize(self._problem(), SolverOptions(1e-5))
        solver.close()
        solver.close()
        self.assertTrue(solver._closed)
        self.assertIsNone(solver._memory)

    def test_partial_initialization_failure_closes_allocated_native_resources(self) -> None:
        solver = FmpyCvodeSolverAdapter()
        with patch("farcel.infrastructure.fmpy.cvode_solver.CVodeInit", return_value=-1):
            with self.assertRaises(RuntimeError):
                solver.initialize(self._problem(), SolverOptions(1e-5))
        self.assertTrue(solver._closed)
        self.assertIsNone(solver._memory)
        self.assertIsNone(solver._states)

    def test_explicit_reset_uses_cvode_reinit_while_integrate_does_not(self) -> None:
        solver = FmpyCvodeSolverAdapter()
        solver.initialize(self._problem(), SolverOptions(1e-5))
        try:
            with patch("farcel.infrastructure.fmpy.cvode_solver.CVodeReInit", return_value=0) as reinitialize:
                solver.reset(0.0, SolverResetReason.OTHER_PROBLEM_CHANGE)
                reinitialize.assert_called_once()
            with patch("farcel.infrastructure.fmpy.cvode_solver.CVodeReInit") as reinitialize:
                result = solver.integrate_to(0.01)
                self.assertIs(result.status, SolverAdvanceStatus.REACHED_TARGET)
                reinitialize.assert_not_called()
        finally:
            solver.close()

    def test_root_result_synchronizes_reached_time_state_without_reset(self) -> None:
        class Problem:
            def __init__(self): self.states = []
            def set_state(self, time, states): self.states.append((time, states))
        solver, problem = FmpyCvodeSolverAdapter(), Problem()
        solver._problem, solver._time, solver._state_count, solver._event_count = problem, 0.0, 2, 2
        solver._states, solver._memory = object(), object()
        def root_return(_memory, _target, _states, reached, _mode):
            reached._obj.value = .06
            return 2
        with patch("farcel.infrastructure.fmpy.cvode_solver.CVode", side_effect=root_return), patch(
            "farcel.infrastructure.fmpy.cvode_solver.NV_DATA_S", return_value=[3.0, 4.0]
        ), patch("farcel.infrastructure.fmpy.cvode_solver.CVodeGetRootInfo", side_effect=lambda _m, roots: (roots.__setitem__(0, 1), roots.__setitem__(1, -1), 0)[2]):
            result = solver.integrate_to(.06)
        self.assertIs(result.status, SolverAdvanceStatus.STATE_EVENT)
        self.assertEqual(result.root_info, (1, -1))
        self.assertEqual(problem.states, [(.06, (3.0, 4.0))])

    def test_invalid_reset_reason_is_rejected_before_native_access(self) -> None:
        solver = FmpyCvodeSolverAdapter()
        solver._problem, solver._options = object(), SolverOptions(1e-5)
        with self.assertRaises(ValueError): solver.reset(0.0, "bad reason")
