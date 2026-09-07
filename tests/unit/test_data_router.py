from __future__ import annotations

import unittest

from farcel.application.data_router import DataRouter
from farcel.application.simulation_orchestrator import SimulationOrchestrator
from farcel.contracts import (
    Connection,
    EngineError,
    ErrorCode,
    GraphSimulationConfig,
    ModelNode,
    ModelNodeConfig,
    PortReference,
    SimulationGraph,
)


def _connection(source_node, source_variable, target_node, target_variable):
    return Connection(PortReference(source_node, source_variable), PortReference(target_node, target_variable))


class _Runtime:
    def __init__(self, value, update=None):
        self.outputs = {"y": value}; self.inputs = []; self.update = update
    def initialize(self): pass
    def set_inputs(self, values): self.inputs.append(dict(values))
    def advance_to(self, target):
        if self.update is not None: self.update(self)
    def read_outputs(self): return self.outputs
    def terminate(self): raise AssertionError("not owned by Phase 4.4")
    def close(self): raise AssertionError("not owned by Phase 4.4")


class DataRouterTests(unittest.TestCase):
    def test_scalars_preserve_values_and_python_types(self):
        router = DataRouter(self._graph(
            _connection("A", "float", "B", "f"), _connection("A", "integer", "B", "i"),
            _connection("A", "boolean", "B", "b"), _connection("A", "string", "B", "s"),
            _connection("A", "enumeration", "B", "e"),
        ))
        source = {"float": 1.25, "integer": -123, "boolean": True, "string": "Farcel", "enumeration": 2}
        routed = router.route({"A": source})["B"]
        self.assertEqual(dict(routed), {"f": 1.25, "i": -123, "b": True, "s": "Farcel", "e": 2})
        self.assertIs(type(routed["b"]), bool)
        self.assertIs(type(routed["e"]), int)

    def test_tuple_arrays_are_routed_without_conversion_or_reshape(self):
        one_dimensional = (1.0, 2.0, 3.0)
        two_dimensional = ((1.0, 2.0), (3.0, 4.0))
        router = DataRouter(self._graph(_connection("A", "vector", "B", "u"), _connection("A", "matrix", "B", "v")))
        routed = router.route({"A": {"vector": one_dimensional, "matrix": two_dimensional}})["B"]
        self.assertIs(routed["u"], one_dimensional)
        self.assertIs(routed["v"], two_dimensional)

    def test_fan_out_multi_input_feedback_self_loop_and_extra_outputs(self):
        router = DataRouter(self._graph(
            _connection("A", "y", "B", "u"), _connection("A", "y", "C", "u"),
            _connection("A", "y", "D", "u"), _connection("B", "z", "C", "v"),
            _connection("B", "z", "A", "feedback"), _connection("A", "y", "A", "self"),
        ))
        routed = router.route({"A": {"y": 1, "extra": "ignored"}, "B": {"z": 2}})
        self.assertEqual({node: dict(inputs) for node, inputs in routed.items()}, {"B": {"u": 1}, "C": {"u": 1, "v": 2}, "D": {"u": 1}, "A": {"feedback": 2, "self": 1}})

    def test_empty_connections_snapshot_is_unmodified_and_results_are_fresh_immutable_mappings(self):
        empty = DataRouter(self._graph())
        self.assertEqual(dict(empty.route({"A": {"y": 1}})), {})
        router = DataRouter(self._graph(_connection("A", "y", "B", "u")))
        snapshot = {"A": {"y": 1}}
        first = router.route(snapshot); second = router.route({"A": {"y": 2}})
        self.assertEqual(snapshot, {"A": {"y": 1}})
        self.assertEqual(first["B"]["u"], 1)
        self.assertEqual(second["B"]["u"], 2)
        with self.assertRaises(TypeError): first["B"] = {}
        with self.assertRaises(TypeError): first["B"]["u"] = 0

    def test_missing_source_node_and_variable_are_output_read_errors_with_diagnostics(self):
        router = DataRouter(self._graph(_connection("A", "y", "B", "u")))
        for snapshot in ({}, {"A": {}}):
            with self.subTest(snapshot=snapshot):
                with self.assertRaises(EngineError) as raised: router.route(snapshot)
                self.assertIs(raised.exception.code, ErrorCode.OUTPUT_READ_ERROR)
                self.assertEqual(raised.exception.details, {"phase": "routing", "source_node_id": "A", "source_variable": "y", "target_node_id": "B", "target_variable": "u"})

    def test_source_dependencies_are_deduplicated_ordered_and_independent_of_recording_selection(self):
        graph = SimulationGraph(
            nodes=(ModelNode("A", "a.fmu", ModelNodeConfig(selected_outputs=())), ModelNode("B", "b.fmu", ModelNodeConfig(selected_outputs=("recorded",))),),
            connections=(
                _connection("A", "y", "B", "u"), _connection("A", "y", "C", "u"),
                _connection("A", "z", "C", "v"), _connection("B", "q", "C", "w"),
            ),
        )
        router = DataRouter(graph)
        self.assertEqual(dict(router.source_outputs_by_node), {"A": ("y", "z"), "B": ("q",)})
        self.assertEqual(graph.nodes[0].config.selected_outputs, ())
        self.assertEqual(graph.nodes[1].config.selected_outputs, ("recorded",))
        with self.assertRaises(TypeError): router.source_outputs_by_node["A"] = ()

    def test_router_plugs_into_orchestrator_and_preserves_jacobi_delay(self):
        graph = self._graph(_connection("A", "y", "B", "u"), _connection("B", "y", "C", "u"))
        router = DataRouter(graph)
        a = _Runtime(1, update=lambda runtime: runtime.outputs.update(y=2))
        b = _Runtime(10, update=lambda runtime: runtime.outputs.update(y=runtime.inputs[-1].get("u", 0)))
        c = _Runtime(100, update=lambda runtime: runtime.outputs.update(y=runtime.inputs[-1].get("u", 0)))
        orchestrator = SimulationOrchestrator(
            (("A", a), ("B", b), ("C", c)),
            GraphSimulationConfig(stop_time=.02, communication_step=.01),
            router.route,
        )
        orchestrator.initialize(); orchestrator.advance_next_checkpoint()
        self.assertEqual((b.inputs[-1]["u"], c.inputs[-1]["u"]), (1, 10))
        orchestrator.advance_next_checkpoint()
        self.assertEqual((b.inputs[-1]["u"], c.inputs[-1]["u"]), (2, 1))

    @staticmethod
    def _graph(*connections):
        return SimulationGraph(connections=connections)
