from __future__ import annotations

import unittest

from farcel.contracts import SolverAdvanceStatus
from farcel.infrastructure.fmpy.cvode_solver import FmpyCvodeSolverAdapter


class CvodeSolverCharacterizationTests(unittest.TestCase):
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
