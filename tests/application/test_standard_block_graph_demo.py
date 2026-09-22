from __future__ import annotations

from dataclasses import replace
import unittest

from farcel import create_backend
from farcel.application import StandardBlockCatalogLoader, StandardBlockFactory
from farcel.contracts import (
    Connection,
    GraphSimulationConfig,
    ModelNode,
    PortReference,
    SimulationGraph,
    SimulationState,
)


class StandardBlockGraphDemoTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog = StandardBlockCatalogLoader().load()
        self.factory = StandardBlockFactory(catalog)

    def _build_graph(self) -> SimulationGraph:
        step = self.factory.create_model_node(
            "farcel.sources.step",
            "step",
            parameter_overrides={
                "initial_value": 0.0,
                "final_value": 5.0,
                "step_time": 0.01,
            },
        )
        gain = self.factory.create_model_node(
            "farcel.math.gain",
            "gain",
            parameter_overrides={"gain": 3.0},
        )
        gain = replace(gain, config=replace(gain.config, selected_outputs=("y",)))

        return SimulationGraph(
            nodes=(step, gain),
            connections=(
                Connection(PortReference("step", "y"), PortReference("gain", "u")),
            ),
        )

    def test_factory_creates_nodes_and_graph_connection(self) -> None:
        graph = self._build_graph()

        self.assertEqual(("step", "gain"), tuple(node.node_id for node in graph.nodes))
        self.assertTrue(all(isinstance(node, ModelNode) for node in graph.nodes))
        self.assertTrue(all(node.model_path.endswith(".fmu") for node in graph.nodes))
        self.assertEqual(
            Connection(PortReference("step", "y"), PortReference("gain", "u")),
            graph.connections[0],
        )

    def test_run_graph_uses_explicit_jacobi_checkpoint_routing(self) -> None:
        graph = self._build_graph()
        config = GraphSimulationConfig(stop_time=0.03, communication_step=0.01)
        backend = create_backend()

        self.assertTrue(backend.validate_graph(graph, config).is_valid)
        result = backend.run_graph(graph, config)

        self.assertIs(SimulationState.COMPLETED, result.completion_state)
        self.assertEqual((0.0, 0.01, 0.02, 0.03), result.timestamps)
        # explicit-Jacobi uses previous checkpoint snapshot routing: the Step
        # transition observed at 0.01 reaches Gain at the 0.02 checkpoint.
        self.assertEqual((0.0, 0.0, 15.0, 15.0), result.node_outputs["gain"]["y"])


if __name__ == "__main__":
    unittest.main()
