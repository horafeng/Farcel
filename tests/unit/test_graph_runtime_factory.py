from __future__ import annotations

import unittest

from farcel.application.graph_runtime_factory import GraphRuntimeBindingsFactory
from farcel.contracts import (
    Connection, EngineError, ErrorCode, GraphSimulationConfig, InterfaceCapability,
    InterfaceType, ModelMetadata, ModelNode, ModelNodeConfig, PortReference,
    SimulationGraph,
)


class _Importer:
    def __init__(self, metadata):
        self.metadata = metadata
        self.loaded = []

    def load(self, path):
        self.loaded.append(str(path))
        return self.metadata[str(path)]


class _Runtime:
    def __init__(self, name, *, close_error=None):
        self.name = name
        self.close_error = close_error
        self.close_calls = 0

    def initialize(self): pass
    def set_inputs(self, values): pass
    def advance_to(self, target_time): pass
    def read_outputs(self): return {}
    def terminate(self): pass
    def close(self):
        self.close_calls += 1
        if self.close_error: raise self.close_error


class _NodeFactory:
    def __init__(self, *, failure_by_model=None, runtime_by_model=None):
        self.failure_by_model = failure_by_model or {}
        self.runtime_by_model = runtime_by_model or {}
        self.calls = []

    def create(self, metadata, config):
        self.calls.append((metadata.model_id, config))
        failure = self.failure_by_model.get(metadata.model_id)
        if failure: raise failure
        return self.runtime_by_model.setdefault(metadata.model_id, _Runtime(metadata.model_id))


def _metadata(name, interfaces=(InterfaceType.CO_SIMULATION,), *, fmi_version="2.0"):
    return ModelMetadata(
        model_id=name, source_path=f"{name}.fmu", fmi_version=fmi_version,
        model_name=name, interface_types=interfaces,
        executable_interface=interfaces[0] if interfaces else None,
        interface_capabilities=tuple(InterfaceCapability(interface, can_execute=True)
                                     for interface in interfaces),
    )


def _graph(nodes, connections=()):
    return SimulationGraph(nodes=nodes, connections=connections)


class GraphRuntimeBindingsFactoryTests(unittest.TestCase):
    def _factory(self, metadata, cs=None, me=None):
        return GraphRuntimeBindingsFactory(_Importer(metadata), cs or _NodeFactory(), me or _NodeFactory())

    def test_loads_metadata_and_returns_bindings_in_declaration_order(self):
        metadata = {f"{name}.fmu": _metadata(name) for name in ("A", "B", "C")}
        cs = _NodeFactory(); factory = self._factory(metadata, cs=cs)
        graph = _graph(tuple(ModelNode(name, f"{name}.fmu") for name in ("A", "B", "C")))

        bindings = factory.create(graph, GraphSimulationConfig(stop_time=.02, communication_step=.01))

        self.assertEqual([node_id for node_id, _ in bindings], ["A", "B", "C"])
        self.assertEqual(factory._importer.loaded, ["A.fmu", "B.fmu", "C.fmu"])
        self.assertEqual([model_id for model_id, _ in cs.calls], ["A", "B", "C"])

    def test_effective_read_set_is_ordered_recording_union_routing_without_mutating_node(self):
        node_a = ModelNode("A", "A.fmu", ModelNodeConfig(selected_outputs=("recorded", "y")))
        node_b = ModelNode("B", "B.fmu")
        graph = _graph((node_a, node_b), (
            Connection(PortReference("A", "y"), PortReference("B", "u")),
            Connection(PortReference("A", "z"), PortReference("B", "v")),
        ))
        cs = _NodeFactory(); factory = self._factory({"A.fmu": _metadata("A"), "B.fmu": _metadata("B")}, cs=cs)

        factory.create(graph, GraphSimulationConfig(schema_version="1.0", start_time=1, stop_time=2, communication_step=.1, output_interval=.2))

        a_config = cs.calls[0][1]
        self.assertEqual(a_config.selected_outputs, ("recorded", "y", "z"))
        self.assertEqual(node_a.config.selected_outputs, ("recorded", "y"))
        self.assertEqual((a_config.schema_version, a_config.start_time, a_config.stop_time,
            a_config.communication_step, a_config.output_interval), ("1.0", 1, 2, .1, .2))

    def test_routing_only_and_uninvolved_nodes_get_their_correct_read_sets(self):
        nodes = (ModelNode("A", "A.fmu"), ModelNode("B", "B.fmu"), ModelNode("C", "C.fmu"))
        graph = _graph(nodes, (Connection(PortReference("A", "y"), PortReference("B", "u")),))
        cs = _NodeFactory(); factory = self._factory({f"{name}.fmu": _metadata(name) for name in ("A", "B", "C")}, cs=cs)

        factory.create(graph, GraphSimulationConfig())

        self.assertEqual([config.selected_outputs for _, config in cs.calls], [("y",), (), ()])
        self.assertEqual(nodes[0].config.selected_outputs, ())

    def test_copies_node_configuration_and_dispatches_cs_only(self):
        config = ModelNodeConfig(parameters={"gain": 2}, initial_inputs={"u": 1},
            input_schedule=(), relative_tolerance=1e-6,
            execution_interface=InterfaceType.CO_SIMULATION)
        cs = _NodeFactory(); me = _NodeFactory()
        factory = self._factory({"A.fmu": _metadata("A")}, cs=cs, me=me)

        factory.create(_graph((ModelNode("A", "A.fmu", config),)), GraphSimulationConfig())

        effective = cs.calls[0][1]
        self.assertEqual((effective.parameters, effective.initial_inputs, effective.input_schedule,
            effective.relative_tolerance, effective.execution_interface),
            (config.parameters, config.initial_inputs, config.input_schedule,
             config.relative_tolerance, InterfaceType.CO_SIMULATION))
        self.assertEqual(me.calls, [])

    def test_dispatches_explicit_model_exchange_and_defaults_fmi2_dual_interface_to_cs(self):
        dual = _metadata("dual", (InterfaceType.CO_SIMULATION, InterfaceType.MODEL_EXCHANGE))
        cs = _NodeFactory(); me = _NodeFactory()
        factory = self._factory({"default.fmu": dual, "me.fmu": dual}, cs=cs, me=me)
        graph = _graph((ModelNode("default", "default.fmu"), ModelNode("me", "me.fmu",
            ModelNodeConfig(execution_interface=InterfaceType.MODEL_EXCHANGE))))

        factory.create(graph, GraphSimulationConfig())

        self.assertEqual([model_id for model_id, _ in cs.calls], ["dual"])
        self.assertEqual([model_id for model_id, _ in me.calls], ["dual"])

    def test_creation_error_closes_prior_returned_runtimes_and_preserves_primary_error(self):
        a = _Runtime("A")
        cs = _NodeFactory(runtime_by_model={"A": a}, failure_by_model={"B": EngineError(ErrorCode.STEP_ERROR, "B failed")})
        factory = self._factory({"A.fmu": _metadata("A"), "B.fmu": _metadata("B")}, cs=cs)
        graph = _graph((ModelNode("A", "A.fmu"), ModelNode("B", "B.fmu"), ModelNode("C", "C.fmu")))

        with self.assertRaises(EngineError) as raised:
            factory.create(graph, GraphSimulationConfig())

        self.assertIs(raised.exception.code, ErrorCode.STEP_ERROR)
        self.assertEqual((raised.exception.details["node_id"], raised.exception.details["model_path"],
            raised.exception.details["phase"]), ("B", "B.fmu", "runtime_creation"))
        self.assertEqual(a.close_calls, 1)
        self.assertEqual([model_id for model_id, _ in cs.calls], ["A", "B"])

    def test_partial_close_failure_is_attached_without_replacing_creation_error(self):
        a = _Runtime("A", close_error=EngineError(ErrorCode.CLEANUP_ERROR, "close A", {"reason": "test"}))
        cs = _NodeFactory(runtime_by_model={"A": a}, failure_by_model={"B": EngineError(ErrorCode.UNSUPPORTED_INTERFACE, "B failed")})
        factory = self._factory({"A.fmu": _metadata("A"), "B.fmu": _metadata("B")}, cs=cs)

        with self.assertRaises(EngineError) as raised:
            factory.create(_graph((ModelNode("A", "A.fmu"), ModelNode("B", "B.fmu"))), GraphSimulationConfig())

        self.assertIs(raised.exception.code, ErrorCode.UNSUPPORTED_INTERFACE)
        self.assertEqual(raised.exception.details["cleanup_failures"], ({"node_id": "A", "phase": "close", "code": "CLEANUP_ERROR", "message": "close A", "details": {"reason": "test"}},))

    def test_ordinary_creation_error_is_stably_wrapped(self):
        cs = _NodeFactory(failure_by_model={"A": RuntimeError("create A")})
        factory = self._factory({"A.fmu": _metadata("A")}, cs=cs)

        with self.assertRaises(EngineError) as raised:
            factory.create(_graph((ModelNode("A", "A.fmu"),)), GraphSimulationConfig())

        self.assertIs(raised.exception.code, ErrorCode.INTERNAL_ERROR)
        self.assertEqual((raised.exception.details["node_id"], raised.exception.details["model_path"],
            raised.exception.details["phase"]), ("A", "A.fmu", "runtime_creation"))
        self.assertIn("create A", raised.exception.details["diagnostic"])

    def test_unresolved_interface_is_stable_composition_error(self):
        metadata = _metadata("A", ())
        factory = self._factory({"A.fmu": metadata})
        with self.assertRaises(EngineError) as raised:
            factory.create(_graph((ModelNode("A", "A.fmu"),)), GraphSimulationConfig())
        self.assertIs(raised.exception.code, ErrorCode.UNSUPPORTED_INTERFACE)
        self.assertEqual(raised.exception.details["phase"], "runtime_creation")
