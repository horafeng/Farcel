from __future__ import annotations

import unittest

from farcel.application.simulation_orchestrator import SimulationOrchestrator
from farcel.contracts import EngineError, ErrorCode, GraphSimulationConfig


class _Runtime:
    def __init__(self, node_id, outputs, operations, *, update=None, failure=None):
        self.node_id = node_id; self.outputs = outputs; self.operations = operations
        self.update = update; self.failure = failure or {}; self.inputs = []; self.targets = []
    def initialize(self): self._run("initialize")
    def set_inputs(self, values): self._run("set"); self.inputs.append(dict(values)); self.operations.append(("set", self.node_id, dict(values)))
    def advance_to(self, target):
        self._run("advance"); self.targets.append(target); self.operations.append(("advance", self.node_id, target))
        if self.update is not None: self.update(self)
    def read_outputs(self): self._run("read"); self.operations.append(("read", self.node_id)); return self.outputs
    def terminate(self): raise AssertionError("Phase 4.3 does not orchestrate terminate")
    def close(self): raise AssertionError("Phase 4.3 does not orchestrate close")
    def _run(self, phase):
        if phase in self.failure: raise self.failure[phase]
        if phase == "initialize": self.operations.append(("initialize", self.node_id))


class SimulationOrchestratorTests(unittest.TestCase):
    def test_initialize_has_all_init_then_all_read_barrier_and_is_idempotent(self):
        operations = []; nodes = self._nodes(operations, {"A": 1, "B": 2, "C": 3})
        routed = []
        orchestrator = self._orchestrator(nodes, route=lambda snapshot: routed.append(snapshot) or {})

        initial = orchestrator.initialize()
        self.assertEqual(operations, [("initialize", "A"), ("initialize", "B"), ("initialize", "C"), ("read", "A"), ("read", "B"), ("read", "C")])
        self.assertEqual(routed, [])
        self.assertIs(orchestrator.initialize(), initial)
        self.assertEqual(len([entry for entry in operations if entry[0] == "initialize"]), 3)

    def test_macro_step_has_route_input_advance_read_phase_barriers_once(self):
        operations = []; nodes = self._nodes(operations, {"A": 1, "B": 2, "C": 3})
        calls = []
        def route(snapshot): calls.append(dict(snapshot)); operations.append(("route",)); return {"B": {"u": snapshot["A"]["y"]}}
        orchestrator = self._orchestrator(nodes, route=route)
        orchestrator.initialize(); operations.clear()

        orchestrator.advance_next_checkpoint()

        self.assertEqual(calls[0]["A"]["y"], 1)
        self.assertEqual([entry[0] for entry in operations], ["route", "set", "set", "set", "advance", "advance", "advance", "read", "read", "read"])
        self.assertEqual(nodes[1][1].inputs, [{"u": 1}])

    def test_a_to_b_to_c_is_explicit_jacobi_with_one_checkpoint_delay(self):
        operations = []
        nodes = self._nodes(
            operations, {"A": 1, "B": 10, "C": 100},
            updates={"A": lambda runtime: runtime.outputs.update(y=2), "B": lambda runtime: runtime.outputs.update(y=runtime.inputs[-1].get("u", 0)), "C": lambda runtime: runtime.outputs.update(y=runtime.inputs[-1].get("u", 0))},
        )
        def route(snapshot): return {"B": {"u": snapshot["A"]["y"]}, "C": {"u": snapshot["B"]["y"]}}
        orchestrator = self._orchestrator(nodes, route=route, steps=2)
        orchestrator.initialize(); orchestrator.advance_next_checkpoint()
        self.assertEqual((nodes[1][1].inputs[-1]["u"], nodes[2][1].inputs[-1]["u"]), (1, 10))
        orchestrator.advance_next_checkpoint()
        self.assertEqual((nodes[1][1].inputs[-1]["u"], nodes[2][1].inputs[-1]["u"]), (2, 1))

    def test_feedback_and_self_loop_use_previous_snapshot(self):
        operations = []
        nodes = self._nodes(operations, {"A": 1, "B": 2}, updates={"A": lambda runtime: runtime.outputs.update(y=runtime.inputs[-1].get("u", 0)), "B": lambda runtime: runtime.outputs.update(y=runtime.inputs[-1].get("u", 0))})
        orchestrator = self._orchestrator(nodes, route=lambda snapshot: {"A": {"u": snapshot["B"]["y"]}, "B": {"u": snapshot["A"]["y"]}})
        orchestrator.initialize(); orchestrator.advance_next_checkpoint()
        self.assertEqual((nodes[0][1].inputs[-1]["u"], nodes[1][1].inputs[-1]["u"]), (2, 1))

        operations = []; self_loop = self._nodes(operations, {"A": 7}, updates={"A": lambda runtime: runtime.outputs.update(y=runtime.inputs[-1]["u"] + 1)})
        loop = self._orchestrator(self_loop, route=lambda snapshot: {"A": {"u": snapshot["A"]["y"]}})
        loop.initialize(); loop.advance_next_checkpoint()
        self.assertEqual((self_loop[0][1].inputs[-1]["u"], self_loop[0][1].outputs["y"]), (7, 8))

    def test_declaration_order_does_not_change_logical_checkpoint_values(self):
        def run(order):
            operations = []; nodes = self._nodes(operations, {"A": 1, "B": 2, "C": 3}, updates={key: lambda runtime: runtime.outputs.update(y=runtime.inputs[-1].get("u", runtime.outputs["y"])) for key in ("A", "B", "C")})
            ordered = tuple(next(node for node in nodes if node[0] == node_id) for node_id in order)
            orchestrator = self._orchestrator(ordered, route=lambda snapshot: {"A": {"u": snapshot["C"]["y"]}, "B": {"u": snapshot["A"]["y"]}, "C": {"u": snapshot["B"]["y"]}})
            return dict(orchestrator.initialize()), dict(orchestrator.advance_next_checkpoint())
        self.assertEqual(run(("A", "B", "C"))[1], run(("C", "B", "A"))[1])

    def test_snapshots_are_structurally_immutable_and_detached_from_runtime_dicts(self):
        operations = []; nodes = self._nodes(operations, {"A": 1}, updates={"A": lambda runtime: runtime.outputs.update(y=2)})
        orchestrator = self._orchestrator(nodes, route=lambda _: {})
        initial = orchestrator.initialize(); orchestrator.advance_next_checkpoint()
        self.assertEqual(initial["A"]["y"], 1)
        with self.assertRaises(TypeError): initial["A"] = {}
        with self.assertRaises(TypeError): initial["A"]["y"] = 0

    def test_checkpoint_index_completion_and_post_completion_guard(self):
        operations = []; nodes = self._nodes(operations, {"A": 1})
        calls = []
        orchestrator = SimulationOrchestrator(nodes, GraphSimulationConfig(start_time=1.0, stop_time=1.3, communication_step=.1), lambda _: calls.append(1) or {})
        orchestrator.initialize()
        for _ in range(3): orchestrator.advance_next_checkpoint()
        self.assertEqual(nodes[0][1].targets, [1.1, 1.2, 1.3])
        self.assertEqual((orchestrator.current_time, orchestrator.completed_steps, orchestrator.is_complete), (1.3, 3, True))
        counts = (len(calls), len(nodes[0][1].inputs), len(nodes[0][1].targets), len([entry for entry in operations if entry[0] == "read"]))
        with self.assertRaises(EngineError) as raised: orchestrator.advance_next_checkpoint()
        self.assertIs(raised.exception.code, ErrorCode.STEP_ERROR)
        self.assertEqual((len(calls), len(nodes[0][1].inputs), len(nodes[0][1].targets), len([entry for entry in operations if entry[0] == "read"])), counts)

    def test_unknown_route_node_and_all_step_failures_leave_scheduler_failed_without_commit(self):
        nodes = self._nodes([], {"A": 1})
        unknown = self._orchestrator(nodes, route=lambda _: {"missing": {"u": 1}}); unknown.initialize()
        with self.assertRaises(EngineError) as raised: unknown.advance_next_checkpoint()
        self.assertIs(raised.exception.code, ErrorCode.INTERNAL_ERROR)
        self.assertTrue(unknown.is_failed)
        with self.assertRaises(EngineError): unknown.advance_next_checkpoint()

        for phase in ("set", "advance", "read"):
            with self.subTest(phase=phase):
                operations = []; runtime = _Runtime("A", {"y": 1}, operations, failure={phase: RuntimeError(phase)})
                orchestrator = self._orchestrator((("A", runtime),), route=lambda _: {})
                if phase == "read":
                    runtime.failure = {}; orchestrator.initialize(); runtime.failure = {"read": RuntimeError("read")}
                else: orchestrator.initialize()
                with self.assertRaisesRegex(RuntimeError, phase): orchestrator.advance_next_checkpoint()
                self.assertEqual((orchestrator.current_time, orchestrator.completed_steps, orchestrator.is_failed), (0.0, 0, True))
                with self.assertRaises(EngineError): orchestrator.advance_next_checkpoint()

    def test_advance_before_initialize_and_initialize_failure_are_terminal(self):
        runtime = _Runtime("A", {"y": 1}, [], failure={"initialize": RuntimeError("init")})
        failed = self._orchestrator((("A", runtime),), route=lambda _: {})
        with self.assertRaises(EngineError): failed.advance_next_checkpoint()
        with self.assertRaisesRegex(RuntimeError, "init"): failed.initialize()
        self.assertTrue(failed.is_failed)
        with self.assertRaises(EngineError): failed.advance_next_checkpoint()

    @staticmethod
    def _nodes(operations, values, *, updates=None):
        updates = updates or {}
        return tuple((node_id, _Runtime(node_id, {"y": value}, operations, update=updates.get(node_id))) for node_id, value in values.items())

    @staticmethod
    def _orchestrator(nodes, *, route, steps=1):
        return SimulationOrchestrator(nodes, GraphSimulationConfig(stop_time=.01 * steps, communication_step=.01), route)
