from dataclasses import FrozenInstanceError, fields
import unittest

from farcel.contracts import (
    Connection,
    GraphSimulationConfig,
    InputUpdate,
    InterfaceType,
    ModelNode,
    ModelNodeConfig,
    PortReference,
    SimulationGraph,
)


class GraphContractTests(unittest.TestCase):
    def test_new_dtos_are_public_contract_exports(self) -> None:
        self.assertEqual(
            (
                PortReference.__name__,
                Connection.__name__,
                ModelNodeConfig.__name__,
                ModelNode.__name__,
                SimulationGraph.__name__,
                GraphSimulationConfig.__name__,
            ),
            (
                "PortReference",
                "Connection",
                "ModelNodeConfig",
                "ModelNode",
                "SimulationGraph",
                "GraphSimulationConfig",
            ),
        )

    def test_port_reference_preserves_endpoint_names(self) -> None:
        reference = PortReference(node_id="source", variable_name="y")

        self.assertEqual(reference.node_id, "source")
        self.assertEqual(reference.variable_name, "y")

    def test_connection_preserves_two_port_references(self) -> None:
        source = PortReference("source", "y")
        target = PortReference("target", "u")

        self.assertEqual(Connection(source, target), Connection(source, target))

    def test_model_node_config_defaults_are_independent(self) -> None:
        first = ModelNodeConfig()
        second = ModelNodeConfig()

        self.assertEqual(first.parameters, {})
        self.assertEqual(first.initial_inputs, {})
        self.assertEqual(first.input_schedule, ())
        self.assertEqual(first.selected_outputs, ())
        self.assertIsNone(first.relative_tolerance)
        self.assertIsNone(first.execution_interface)
        self.assertIsNot(first.parameters, second.parameters)
        self.assertIsNot(first.initial_inputs, second.initial_inputs)

    def test_model_node_config_reuses_existing_input_and_interface_contracts(self) -> None:
        update = InputUpdate(time=0.1, values={"u": 1.0})
        config = ModelNodeConfig(
            input_schedule=(update,),
            execution_interface=InterfaceType.CO_SIMULATION,
        )

        self.assertEqual(config.input_schedule, (update,))
        self.assertIs(config.execution_interface, InterfaceType.CO_SIMULATION)

    def test_model_node_config_has_no_graph_global_time_fields(self) -> None:
        field_names = {item.name for item in fields(ModelNodeConfig)}

        self.assertTrue(
            {"start_time", "stop_time", "communication_step", "output_interval"}
            .isdisjoint(field_names)
        )

    def test_model_node_preserves_path_and_supplied_config(self) -> None:
        config = ModelNodeConfig(selected_outputs=("y",))
        node = ModelNode(node_id="source", model_path="source.fmu", config=config)

        self.assertEqual(node.node_id, "source")
        self.assertEqual(node.model_path, "source.fmu")
        self.assertIs(node.config, config)

    def test_model_node_default_config_is_constructible(self) -> None:
        node = ModelNode(node_id="source", model_path="source.fmu")

        self.assertEqual(node.config, ModelNodeConfig())

    def test_simulation_graph_preserves_two_node_connection(self) -> None:
        source = ModelNode(node_id="source", model_path="source.fmu")
        target = ModelNode(node_id="target", model_path="target.fmu")
        connection = Connection(PortReference("source", "y"), PortReference("target", "u"))
        graph = SimulationGraph(nodes=(source, target), connections=(connection,))

        self.assertEqual(graph.nodes, (source, target))
        self.assertEqual(graph.connections, (connection,))

    def test_simulation_graph_accepts_feedback_as_declarative_dto(self) -> None:
        graph = SimulationGraph(
            connections=(
                Connection(PortReference("a", "y"), PortReference("b", "u")),
                Connection(PortReference("b", "y"), PortReference("a", "u")),
            )
        )

        self.assertEqual(len(graph.connections), 2)

    def test_graph_simulation_config_defaults_and_explicit_values(self) -> None:
        self.assertEqual(
            GraphSimulationConfig(),
            GraphSimulationConfig(
                schema_version="1.0",
                start_time=0.0,
                stop_time=1.0,
                communication_step=0.01,
                output_interval=None,
            ),
        )
        self.assertEqual(
            GraphSimulationConfig("2.0", 1.0, 3.0, 0.5, 1.0).stop_time,
            3.0,
        )

    def test_graph_simulation_config_contains_only_global_fields(self) -> None:
        self.assertEqual(
            tuple(item.name for item in fields(GraphSimulationConfig)),
            ("schema_version", "start_time", "stop_time", "communication_step", "output_interval"),
        )

    def test_graph_contracts_are_frozen(self) -> None:
        reference = PortReference(node_id="source", variable_name="y")

        with self.assertRaises(FrozenInstanceError):
            reference.node_id = "other"

    def test_dtos_do_not_perform_phase_4_1_semantic_validation(self) -> None:
        graph = SimulationGraph(
            nodes=(
                ModelNode(node_id="", model_path="first.fmu"),
                ModelNode(node_id="", model_path="second.fmu"),
            )
        )
        timing = GraphSimulationConfig(start_time=1.0, stop_time=0.0)

        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(timing.stop_time, 0.0)
