from __future__ import annotations

import unittest

from farcel.application.graph_runner import GraphSimulationRunner
from farcel.contracts import (
    Connection, EngineError, ErrorCode, GraphSimulationConfig, ModelNode,
    ModelNodeConfig, PortReference, RunControl, SimulationGraph, SimulationState,
)


class _Runtime:
    """Observable fake ModelNodeRuntime; failures are local to this test module."""

    def __init__(self, node_id, operations, *, outputs=None, failures=None,
                 on_advance=None, on_initialize=None):
        self.node_id = node_id
        self.operations = operations
        self.outputs = dict(outputs or {"y": node_id, "recorded": node_id})
        self.failures = dict(failures or {})
        self.on_advance = on_advance
        self.on_initialize = on_initialize
        self.inputs = []
        self.terminated = self.closed = 0

    def initialize(self):
        self.operations.append(("initialize", self.node_id))
        if self.on_initialize:
            self.on_initialize(self)
        self._fail("initialize")

    def set_inputs(self, values):
        self.operations.append(("set", self.node_id, dict(values)))
        self._fail("input")
        self.inputs.append(dict(values))

    def advance_to(self, target):
        self.operations.append(("advance", self.node_id, target))
        self._fail("advance")
        if self.on_advance:
            self.on_advance(self, target)

    def read_outputs(self):
        self.operations.append(("read", self.node_id))
        self._fail("read")
        return self.outputs

    def terminate(self):
        self.terminated += 1
        self.operations.append(("terminate", self.node_id))
        self._fail("terminate")

    def close(self):
        self.closed += 1
        self.operations.append(("close", self.node_id))
        self._fail("close")

    def _fail(self, phase):
        failure = self.failures.get(phase)
        if isinstance(failure, list):
            failure = failure.pop(0) if failure else None
        if callable(failure):
            failure = failure(self)
        if failure is not None:
            raise failure


class GraphRunnerTests(unittest.TestCase):
    def _graph(self, *, selected=None, connections=(), ids=("A", "B", "C")):
        selected = selected or {}
        return SimulationGraph(
            nodes=tuple(ModelNode(node_id, node_id, ModelNodeConfig(
                selected_outputs=selected.get(node_id, ()))) for node_id in ids),
            connections=connections,
        )

    @staticmethod
    def _config(*, stop=.03, interval=None):
        return GraphSimulationConfig(stop_time=stop, communication_step=.01,
                                     output_interval=interval)

    @staticmethod
    def _factory(operations, bindings):
        def factory():
            operations.append(("factory",))
            return bindings
        return factory

    @staticmethod
    def _cleanup_tail():
        return [("terminate", "A"), ("terminate", "B"), ("terminate", "C"),
                ("close", "A"), ("close", "B"), ("close", "C")]

    def test_prestart_cancel_does_not_create_or_cleanup_a_runtime(self):
        control = RunControl(); control.request_stop(); operations = []
        with self.assertRaises(EngineError) as raised:
            GraphSimulationRunner(self._factory(operations, ())).run(
                self._graph(), self._config(), control=control)
        self.assertIs(raised.exception.code, ErrorCode.CANCELLED)
        self.assertEqual(operations, [])

    def test_inventory_mismatch_reports_ids_and_cleans_every_returned_runtime(self):
        operations = []; a = _Runtime("A", operations); c = _Runtime("C", operations)
        with self.assertRaises(EngineError) as raised:
            GraphSimulationRunner(self._factory(operations, (("A", a), ("C", c)))).run(
                self._graph(ids=("A", "B")), self._config())
        error = raised.exception
        self.assertIs(error.code, ErrorCode.INTERNAL_ERROR)
        self.assertEqual(error.details, {"phase": "runtime_binding",
            "expected_node_ids": ("A", "B"), "actual_node_ids": ("A", "C")})
        self.assertEqual(operations, [("factory",), ("terminate", "A"),
            ("terminate", "C"), ("close", "A"), ("close", "C")])

    def test_stop_during_initialization_finishes_t0_barrier_then_stops(self):
        operations = []; control = RunControl()
        a = _Runtime("A", operations, on_initialize=lambda _: control.request_stop())
        b = _Runtime("B", operations); c = _Runtime("C", operations)
        result = GraphSimulationRunner(self._factory(operations, (("A", a), ("B", b),
            ("C", c)))).run(self._graph(), self._config(), control=control)
        self.assertEqual(result.completion_state, SimulationState.STOPPED)
        self.assertEqual((result.completed_steps, result.final_time, result.timestamps),
                         (0, 0, (0,)))
        self.assertEqual(operations[:7], [("factory",), ("initialize", "A"),
            ("initialize", "B"), ("initialize", "C"), ("read", "A"),
            ("read", "B"), ("read", "C")])
        self.assertEqual(operations[-6:], self._cleanup_tail())

    def test_mid_macrostep_stop_completes_all_nodes_and_snapshot_before_stopping(self):
        operations = []; control = RunControl()
        a = _Runtime("A", operations, on_advance=lambda _, __: control.request_stop())
        bindings = (("A", a), ("B", _Runtime("B", operations)),
                    ("C", _Runtime("C", operations)))
        result = GraphSimulationRunner(self._factory(operations, bindings)).run(
            self._graph(), self._config(), control=control)
        first_step = operations[7:16]
        self.assertEqual([entry[:2] for entry in first_step], [
            ("set", "A"), ("set", "B"), ("set", "C"), ("advance", "A"),
            ("advance", "B"), ("advance", "C"), ("read", "A"),
            ("read", "B"), ("read", "C")])
        self.assertEqual((result.completion_state, result.completed_steps, result.final_time),
                         (SimulationState.STOPPED, 1, .01))
        self.assertEqual(operations[-6:], self._cleanup_tail())

    def test_normal_progress_is_global_and_terminal_sample_count_matches_result(self):
        operations = []; progress = []
        bindings = tuple((node_id, _Runtime(node_id, operations))
                         for node_id in ("A", "B", "C"))
        result = GraphSimulationRunner(self._factory(operations, bindings)).run(
            self._graph(), self._config(interval=.02), on_progress=progress.append)
        self.assertEqual((progress[0].state, progress[0].current_time,
            progress[0].completed_steps, progress[0].sample_count, progress[0].fraction),
            (SimulationState.RUNNING, 0, 0, 1, 0))
        self.assertTrue(any(item.state is SimulationState.RUNNING and item.current_time == .02
                            for item in progress))
        terminal = progress[-1]
        self.assertEqual((terminal.state, terminal.current_time, terminal.completed_steps,
            terminal.sample_count, terminal.fraction),
            (SimulationState.COMPLETED, .03, 3, result.sample_count, 1.0))

    def test_stopped_progress_reports_stopped_result_not_completion(self):
        operations = []; control = RunControl(); progress = []
        a = _Runtime("A", operations, on_advance=lambda _, __: control.request_stop())
        bindings = (("A", a), ("B", _Runtime("B", operations)),
                    ("C", _Runtime("C", operations)))
        result = GraphSimulationRunner(self._factory(operations, bindings)).run(
            self._graph(), self._config(), control=control, on_progress=progress.append)
        terminal = progress[-1]
        self.assertEqual((terminal.state, terminal.current_time, terminal.completed_steps,
            terminal.sample_count), (SimulationState.STOPPED, result.final_time,
            result.completed_steps, result.sample_count))
        self.assertLess(terminal.fraction, 1.0)

    def test_initial_progress_callback_failure_is_wrapped_and_cleans_up(self):
        operations = []; bindings = tuple((node_id, _Runtime(node_id, operations))
                                          for node_id in ("A", "B", "C"))
        def callback(_): raise RuntimeError("callback")
        with self.assertRaises(EngineError) as raised:
            GraphSimulationRunner(self._factory(operations, bindings)).run(
                self._graph(), self._config(), on_progress=callback)
        self.assertIs(raised.exception.code, ErrorCode.INTERNAL_ERROR)
        self.assertEqual(raised.exception.details["phase"], "progress_callback")
        self.assertEqual(raised.exception.details["current_time"], 0)
        self.assertIn("callback", raised.exception.details["diagnostic"])
        self.assertEqual(operations[-6:], self._cleanup_tail())

    def test_terminal_progress_callback_failure_does_not_repeat_cleanup(self):
        operations = []; bindings = tuple((node_id, _Runtime(node_id, operations))
                                          for node_id in ("A", "B", "C"))
        def callback(item):
            if item.state is SimulationState.COMPLETED: raise RuntimeError("callback")
        with self.assertRaises(EngineError) as raised:
            GraphSimulationRunner(self._factory(operations, bindings)).run(
                self._graph(), self._config(), on_progress=callback)
        self.assertIs(raised.exception.code, ErrorCode.INTERNAL_ERROR)
        self.assertEqual(raised.exception.details["phase"], "progress_callback")
        for _, runtime in bindings: self.assertEqual((runtime.terminated, runtime.closed), (1, 1))

    def test_initialize_errors_are_attributed_and_cleanup_every_binding(self):
        for failure in (EngineError(ErrorCode.INITIALIZATION_ERROR, "init"), RuntimeError("init")):
            with self.subTest(failure=type(failure).__name__):
                operations = []; bindings = (("A", _Runtime("A", operations)),
                    ("B", _Runtime("B", operations, failures={"initialize": failure})),
                    ("C", _Runtime("C", operations)))
                with self.assertRaises(EngineError) as raised:
                    GraphSimulationRunner(self._factory(operations, bindings)).run(self._graph(), self._config())
                error = raised.exception
                self.assertIs(error.code, ErrorCode.INITIALIZATION_ERROR if isinstance(failure, EngineError) else ErrorCode.INTERNAL_ERROR)
                self.assertEqual((error.details["node_id"], error.details["phase"], error.details["current_time"]), ("B", "initialize", 0))
                self.assertEqual(operations[-6:], self._cleanup_tail())

    def test_input_and_advance_errors_preserve_code_phase_and_no_macrostep_commit(self):
        cases = (("input", ErrorCode.INPUT_SET_ERROR, "input"),
                 ("advance", ErrorCode.STEP_ERROR, "advance"))
        for failure_phase, code, expected_phase in cases:
            with self.subTest(phase=failure_phase):
                operations = []; bindings = tuple((name, _Runtime(name, operations,
                    failures={failure_phase: EngineError(code, failure_phase)} if name == "B" else {}))
                    for name in ("A", "B", "C"))
                with self.assertRaises(EngineError) as raised:
                    GraphSimulationRunner(self._factory(operations, bindings)).run(self._graph(), self._config())
                error = raised.exception
                self.assertIs(error.code, code)
                self.assertEqual((error.details["node_id"], error.details["phase"], error.details["current_time"]), ("B", expected_phase, 0))
                if failure_phase == "advance": self.assertEqual(error.details["target_time"], .01)
                self.assertEqual(operations[-6:], self._cleanup_tail())

    def test_initial_and_checkpoint_output_read_errors_are_attributed(self):
        cases = (({"read": EngineError(ErrorCode.OUTPUT_READ_ERROR, "read")}, 0, "initial_output_read"),
                 ({"read": [None, EngineError(ErrorCode.OUTPUT_READ_ERROR, "read")]}, .01, "checkpoint_output_read"))
        for failures, current_time, phase in cases:
            with self.subTest(phase=phase):
                operations = []; bindings = tuple((name, _Runtime(name, operations,
                    failures=failures if name == "B" else {})) for name in ("A", "B", "C"))
                with self.assertRaises(EngineError) as raised:
                    GraphSimulationRunner(self._factory(operations, bindings)).run(self._graph(), self._config())
                error = raised.exception
                self.assertIs(error.code, ErrorCode.OUTPUT_READ_ERROR)
                self.assertEqual((error.details["node_id"], error.details["phase"], error.details["current_time"]), ("B", phase, current_time))
                self.assertEqual(operations[-6:], self._cleanup_tail())

    def test_routing_failure_retains_connection_diagnostics_and_current_time(self):
        operations = []; bindings = tuple((name, _Runtime(name, operations,
            outputs={"recorded": name})) for name in ("A", "B", "C"))
        graph = self._graph(connections=(Connection(PortReference("A", "y"),
                                                   PortReference("B", "u")),))
        with self.assertRaises(EngineError) as raised:
            GraphSimulationRunner(self._factory(operations, bindings)).run(graph, self._config())
        error = raised.exception
        self.assertIs(error.code, ErrorCode.OUTPUT_READ_ERROR)
        self.assertEqual(error.details, {"phase": "routing", "source_node_id": "A",
            "source_variable": "y", "target_node_id": "B", "target_variable": "u",
            "current_time": 0})
        self.assertEqual(operations[-6:], self._cleanup_tail())

    def test_result_sampling_missing_selected_output_is_stable_error(self):
        operations = []; bindings = tuple((name, _Runtime(name, operations,
            outputs={"y": name})) for name in ("A", "B", "C"))
        with self.assertRaises(EngineError) as raised:
            GraphSimulationRunner(self._factory(operations, bindings)).run(
                self._graph(selected={"A": ("recorded",)}), self._config())
        self.assertIs(raised.exception.code, ErrorCode.OUTPUT_READ_ERROR)
        self.assertEqual(raised.exception.details, {"node_id": "A", "variable_name": "recorded",
            "phase": "result_sampling", "current_time": 0})
        self.assertEqual(operations[-6:], self._cleanup_tail())

    def test_routing_only_output_is_not_recorded_and_selected_is_separate(self):
        operations = []; a = _Runtime("A", operations, outputs={"y": 1, "recorded": 2})
        bindings = (("A", a), ("B", _Runtime("B", operations)), ("C", _Runtime("C", operations)))
        graph = self._graph(selected={"A": ("recorded",)}, connections=(
            Connection(PortReference("A", "y"), PortReference("B", "u")),))
        result = GraphSimulationRunner(self._factory(operations, bindings)).run(graph, self._config(stop=.01))
        self.assertEqual(set(result.node_outputs["A"]), {"recorded"})
        self.assertEqual(result.node_outputs["B"], {})

    def test_routing_only_output_is_not_recorded_when_node_selects_nothing(self):
        operations = []; a = _Runtime("A", operations, outputs={"y": 1})
        bindings = (("A", a), ("B", _Runtime("B", operations)), ("C", _Runtime("C", operations)))
        graph = self._graph(connections=(Connection(PortReference("A", "y"),
                                                   PortReference("B", "u")),))
        result = GraphSimulationRunner(self._factory(operations, bindings)).run(graph, self._config(stop=.01))
        self.assertEqual(result.node_outputs["A"], {})

    def test_endpoint_sampling_normal_stop_and_no_duplicate_endpoint(self):
        cases = ((.03, .02, None, (0, .02, .03)), (.04, .02, None, (0, .02, .04)),
                 (.03, .03, "stop", (0, .02)))
        for stop, interval, stop_mode, expected in cases:
            with self.subTest(stop=stop, interval=interval, stopped=bool(stop_mode)):
                operations = []; control = RunControl() if stop_mode else None
                def stop_at_two(_, target):
                    if target == .02: control.request_stop()
                a = _Runtime("A", operations, on_advance=stop_at_two if control else None)
                bindings = (("A", a), ("B", _Runtime("B", operations)),
                            ("C", _Runtime("C", operations)))
                result = GraphSimulationRunner(self._factory(operations, bindings)).run(
                    self._graph(), self._config(stop=stop, interval=interval), control=control)
                self.assertEqual(result.timestamps, expected)

    def test_cleanup_is_declaration_order_terminate_all_then_close_all(self):
        operations = []; bindings = tuple((name, _Runtime(name, operations))
                                          for name in ("A", "B", "C"))
        GraphSimulationRunner(self._factory(operations, bindings)).run(self._graph(), self._config(stop=.01))
        self.assertEqual(operations[-6:], self._cleanup_tail())

    def test_cleanup_is_best_effort_and_primary_error_wins_with_ordered_diagnostics(self):
        operations = []
        a = _Runtime("A", operations, failures={"terminate": EngineError(ErrorCode.TERMINATION_ERROR, "terminate-a", {"reason": "a"}), "close": EngineError(ErrorCode.CLEANUP_ERROR, "close-a", {"reason": "a"})})
        b = _Runtime("B", operations, failures={"advance": EngineError(ErrorCode.STEP_ERROR, "step-b"), "terminate": EngineError(ErrorCode.TERMINATION_ERROR, "terminate-b", {"reason": "b"})})
        c = _Runtime("C", operations, failures={"close": RuntimeError("close-c")})
        with self.assertRaises(EngineError) as raised:
            GraphSimulationRunner(self._factory(operations, (("A", a), ("B", b), ("C", c)))).run(self._graph(), self._config())
        error = raised.exception
        self.assertIs(error.code, ErrorCode.STEP_ERROR)
        self.assertEqual(operations[-6:], self._cleanup_tail())
        failures = error.details["cleanup_failures"]
        self.assertEqual([(item["node_id"], item["phase"], item["code"]) for item in failures],
            [("A", "terminate", "TERMINATION_ERROR"), ("B", "terminate", "TERMINATION_ERROR"),
             ("A", "close", "CLEANUP_ERROR"), ("C", "close", "CLEANUP_ERROR")])
        self.assertEqual(failures[-1]["message"], "close-c")
        self.assertEqual(failures[-1]["details"], {})
        self.assertEqual(failures[0]["details"], {"reason": "a"})

    def test_terminate_failure_still_closes_same_node_and_success_cleanup_failure_is_primary(self):
        cases = (("terminate", ErrorCode.TERMINATION_ERROR, EngineError(ErrorCode.TERMINATION_ERROR, "bad terminate")),
                 ("close", ErrorCode.CLEANUP_ERROR, EngineError(ErrorCode.CLEANUP_ERROR, "bad close")),
                 ("terminate", ErrorCode.TERMINATION_ERROR, RuntimeError("bad terminate")),
                 ("close", ErrorCode.CLEANUP_ERROR, RuntimeError("bad close")))
        for phase, code, failure in cases:
            with self.subTest(phase=phase, failure=type(failure).__name__):
                operations = []; a = _Runtime("A", operations, failures={phase: failure})
                bindings = (("A", a), ("B", _Runtime("B", operations)),
                            ("C", _Runtime("C", operations)))
                with self.assertRaises(EngineError) as raised:
                    GraphSimulationRunner(self._factory(operations, bindings)).run(self._graph(), self._config(stop=.01))
                self.assertIs(raised.exception.code, code)
                self.assertEqual(operations[-6:], self._cleanup_tail())
                self.assertEqual(a.closed, 1)

    def test_binding_mismatch_primary_keeps_cleanup_failures(self):
        operations = []; a = _Runtime("A", operations, failures={"terminate": EngineError(ErrorCode.TERMINATION_ERROR, "bad")})
        with self.assertRaises(EngineError) as raised:
            GraphSimulationRunner(self._factory(operations, (("A", a),))).run(
                self._graph(ids=("A", "B")), self._config())
        self.assertIs(raised.exception.code, ErrorCode.INTERNAL_ERROR)
        self.assertEqual(raised.exception.details["cleanup_failures"][0]["phase"], "terminate")
        self.assertEqual(a.closed, 1)

    def test_runtime_factory_errors_preserve_engine_error_or_wrap_ordinary_error_without_cleanup(self):
        expected = EngineError(ErrorCode.CONFIG_ERROR, "factory config")
        with self.assertRaises(EngineError) as raised:
            GraphSimulationRunner(lambda: (_ for _ in ()).throw(expected)).run(self._graph(), self._config())
        self.assertIs(raised.exception.code, ErrorCode.CONFIG_ERROR)
        with self.assertRaises(EngineError) as raised:
            GraphSimulationRunner(lambda: (_ for _ in ()).throw(RuntimeError("factory"))).run(self._graph(), self._config())
        self.assertIs(raised.exception.code, ErrorCode.INTERNAL_ERROR)
        self.assertIn("factory", raised.exception.details["diagnostic"])
