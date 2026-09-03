from __future__ import annotations

import math
from ctypes import POINTER, byref, c_int, c_void_p

from fmpy.sundials.cvode import CV_BDF, CV_NORMAL, CV_ROOT_RETURN, CV_SUCCESS, CVode, CVodeCreate, CVodeFree, CVodeGetRootInfo, CVodeInit, CVodeReInit, CVodeRootInit, CVodeSetMaxNumSteps, CVodeSetMaxStep, CVodeSetNoInactiveRootWarn, CVodeSVtolerances, CVRhsFn, CVRootFn
from fmpy.sundials.cvode_ls import CVodeSetLinearSolver
from fmpy.sundials.libraries import sundials_core
from fmpy.sundials.nvector_serial import N_VDestroy_Serial, N_VNew_Serial, NV_DATA_S
from fmpy.sundials.sundials_context import SUNContext_Create
from fmpy.sundials.sundials_types import SUN_COMM_NULL, SUNContext, sunrealtype
from fmpy.sundials.sunlinsol_dense import SUNLinSol_Dense
from fmpy.sundials.sunmatrix_dense import SUNDenseMatrix

from farcel.contracts.models import SolverAdvanceResult, SolverAdvanceStatus, SolverOptions, SolverResetReason
from farcel.contracts.ports import ModelExchangeProblem, SolverAdapter, SolverFactory

# FMPy 0.3.31 already loads SUNDIALS 7. It omits these three destructor
# wrappers, so bind the existing FMPy core handle instead of loading a library.
_SUNContext_Free = sundials_core.SUNContext_Free
_SUNContext_Free.argtypes, _SUNContext_Free.restype = [POINTER(SUNContext)], c_int
_SUNMatDestroy = sundials_core.SUNMatDestroy
_SUNMatDestroy.argtypes, _SUNMatDestroy.restype = [c_void_p], None
_SUNLinSolFree = sundials_core.SUNLinSolFree
_SUNLinSolFree.argtypes, _SUNLinSolFree.restype = [c_void_p], c_int


class FmpyCvodeSolverAdapter:
    """Low-level FMPy SUNDIALS bridge with Farcel-owned lifecycle semantics."""

    def __init__(self) -> None:
        self._context = SUNContext()
        self._states = self._abstol = self._memory = self._matrix = self._linear_solver = None
        self._problem: ModelExchangeProblem | None = None
        self._options: SolverOptions | None = None
        self._time: float | None = None
        self._state_count = self._native_count = self._event_count = 0
        self._rhs_callback = self._root_callback = None
        self._callback_failure: str | None = None
        self._closed = False

    def initialize(self, problem: ModelExchangeProblem, options: SolverOptions) -> None:
        if self._closed or self._problem is not None:
            raise RuntimeError("CVode solver cannot be initialized in its current state")
        time, states, nominals, event_count = problem.get_initial_time(), problem.get_initial_states(), problem.get_nominals(), problem.get_event_indicator_count()
        if not math.isfinite(time) or not math.isfinite(options.relative_tolerance) or options.relative_tolerance <= 0:
            raise ValueError("initial time and relative tolerance must be finite")
        if options.maximum_step is not None and (not math.isfinite(options.maximum_step) or options.maximum_step <= 0):
            raise ValueError("maximum_step must be finite and positive")
        if len(states) != len(nominals) or event_count < 0 or any(not math.isfinite(x) or x <= 0 for x in nominals):
            raise ValueError("invalid Model Exchange state nominals or event count")
        self._problem, self._options, self._time = problem, options, time
        self._state_count, self._native_count, self._event_count = len(states), max(1, len(states)), event_count
        try:
            self._require(SUNContext_Create(SUN_COMM_NULL, byref(self._context)))
            self._states, self._abstol = N_VNew_Serial(self._native_count, self._context), N_VNew_Serial(self._native_count, self._context)
            if not self._states or not self._abstol: raise RuntimeError("SUNDIALS vector allocation failed")
            data, tolerance = NV_DATA_S(self._states), NV_DATA_S(self._abstol)
            if self._state_count == 0: data[0], tolerance[0] = 1.0, options.relative_tolerance
            else:
                for i, value in enumerate(states): data[i], tolerance[i] = value, nominals[i] * options.relative_tolerance
            self._rhs_callback, self._root_callback = CVRhsFn(self._rhs), CVRootFn(self._root)
            self._memory = CVodeCreate(CV_BDF, self._context)
            if not self._memory: raise RuntimeError("CVodeCreate returned no memory")
            self._require(CVodeInit(self._memory, self._rhs_callback, time, self._states))
            self._require(CVodeSVtolerances(self._memory, options.relative_tolerance, self._abstol))
            self._require(CVodeRootInit(self._memory, event_count, self._root_callback))
            self._matrix = SUNDenseMatrix(self._native_count, self._native_count, self._context)
            self._linear_solver = SUNLinSol_Dense(self._states, self._matrix, self._context)
            self._require(CVodeSetLinearSolver(self._memory, self._linear_solver, self._matrix))
            self._require(CVodeSetMaxStep(self._memory, options.maximum_step if options.maximum_step is not None else math.inf))
            self._require(CVodeSetMaxNumSteps(self._memory, 500)); self._require(CVodeSetNoInactiveRootWarn(self._memory))
        except Exception:
            self.close(); raise

    def integrate_to(self, target_time: float) -> SolverAdvanceResult:
        if self._problem is None or self._time is None: raise RuntimeError("CVode solver is not initialized")
        if not math.isfinite(target_time) or (target_time < self._time and not math.isclose(target_time, self._time, rel_tol=1e-12, abs_tol=1e-12)):
            return SolverAdvanceResult(self._time, SolverAdvanceStatus.FAILED, failure_message="target_time must be finite and not precede solver time")
        if math.isclose(target_time, self._time, rel_tol=1e-12, abs_tol=1e-12): return SolverAdvanceResult(self._time, SolverAdvanceStatus.REACHED_TARGET)
        self._callback_failure = None; reached = sunrealtype(0.0)
        flag = CVode(self._memory, target_time, self._states, byref(reached), CV_NORMAL)
        if flag < CV_SUCCESS: return SolverAdvanceResult(self._time, SolverAdvanceStatus.FAILED, failure_message=self._callback_failure or f"CVode failed with code {flag}")
        values = tuple(float(NV_DATA_S(self._states)[i]) for i in range(self._state_count))
        self._problem.set_state(float(reached.value), values); self._time = float(reached.value)
        if flag == CV_ROOT_RETURN:
            roots = (c_int * self._event_count)()
            if self._event_count and CVodeGetRootInfo(self._memory, roots) != CV_SUCCESS: return SolverAdvanceResult(self._time, SolverAdvanceStatus.FAILED, failure_message="CVodeGetRootInfo failed")
            return SolverAdvanceResult(self._time, SolverAdvanceStatus.STATE_EVENT, tuple(int(x) for x in roots))
        if flag == CV_SUCCESS: return SolverAdvanceResult(self._time, SolverAdvanceStatus.REACHED_TARGET)
        return SolverAdvanceResult(self._time, SolverAdvanceStatus.FAILED, failure_message=f"unexpected CVode return code {flag}")

    def reset(self, time: float, reason: SolverResetReason) -> None:
        if self._problem is None or self._options is None: raise RuntimeError("CVode solver is not initialized")
        if not isinstance(reason, SolverResetReason) or not math.isfinite(time): raise ValueError("reset requires finite time and SolverResetReason")
        states, nominals = self._problem.get_initial_states(), self._problem.get_nominals()
        if len(states) != self._state_count or len(nominals) != self._state_count: raise ValueError("reset dimensions changed")
        data, tolerance = NV_DATA_S(self._states), NV_DATA_S(self._abstol)
        if self._state_count == 0: data[0], tolerance[0] = 1.0, self._options.relative_tolerance
        else:
            for i, value in enumerate(states): data[i], tolerance[i] = value, nominals[i] * self._options.relative_tolerance
        self._require(CVodeReInit(self._memory, time, self._states)); self._require(CVodeSVtolerances(self._memory, self._options.relative_tolerance, self._abstol))
        self._problem.set_state(time, states); self._time = time

    def close(self) -> None:
        if self._closed: return
        if self._memory: CVodeFree(byref(c_void_p(self._memory))); self._memory = None
        if self._linear_solver: _SUNLinSolFree(self._linear_solver); self._linear_solver = None
        if self._matrix: _SUNMatDestroy(self._matrix); self._matrix = None
        if self._abstol: N_VDestroy_Serial(self._abstol); self._abstol = None
        if self._states: N_VDestroy_Serial(self._states); self._states = None
        if self._context: _SUNContext_Free(byref(self._context)); self._context = SUNContext()
        self._rhs_callback = self._root_callback = self._problem = self._options = None; self._closed = True

    def _rhs(self, time, states, derivatives, _):
        try:
            values = tuple(float(NV_DATA_S(states)[i]) for i in range(self._state_count)); self._problem.set_state(float(time), values)
            result = self._problem.get_derivatives()
            if len(result) != self._state_count: raise ValueError("derivative count mismatch")
            output = NV_DATA_S(derivatives)
            if self._state_count == 0: output[0] = 0.0
            else:
                for i, value in enumerate(result): output[i] = value
            return 0
        except Exception as exc: self._callback_failure = str(exc); return -1

    def _root(self, time, states, roots, _):
        try:
            values = tuple(float(NV_DATA_S(states)[i]) for i in range(self._state_count)); self._problem.set_state(float(time), values)
            indicators = self._problem.get_event_indicators()
            if len(indicators) != self._event_count: raise ValueError("event indicator count mismatch")
            for i, value in enumerate(indicators): roots[i] = value
            return 0
        except Exception as exc: self._callback_failure = str(exc); return -1

    @staticmethod
    def _require(status: int) -> None:
        if status != CV_SUCCESS: raise RuntimeError(f"SUNDIALS call failed with code {status}")


class FmpyCvodeSolverFactory:
    def create(self) -> SolverAdapter: return FmpyCvodeSolverAdapter()
