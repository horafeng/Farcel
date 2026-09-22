from __future__ import annotations

from dataclasses import replace
import hashlib
from importlib import resources
import unittest

from farcel import create_backend
from farcel.application import StandardBlockCatalogLoader, StandardBlockFactory
from farcel.contracts import (
    Connection,
    GraphSimulationConfig,
    InterfaceType,
    PortReference,
    SimulationConfig,
    SimulationGraph,
    SimulationState,
)


class StandardMathFmuAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = StandardBlockCatalogLoader().load()
        self.assets = resources.files("farcel.standard_library").joinpath("assets")

    def test_multi_input_descriptors_and_assets_match_sha256(self) -> None:
        for block_id, expected_asset, expected_display_name in (
            ("farcel.math.sum", "math/sum/Sum.fmu", "加法器"),
            ("farcel.math.product", "math/product/Product.fmu", "乘法器"),
        ):
            with self.subTest(block_id=block_id):
                block = self.catalog.get_block(block_id)
                asset = self.assets.joinpath(block.fmu_asset)

                self.assertEqual(expected_asset, block.fmu_asset)
                self.assertEqual(expected_display_name, block.display_name)
                self.assertEqual((), block.parameters)
                self.assertEqual(("u1", "u2"), tuple(port.variable_name for port in block.input_ports))
                self.assertEqual(("y",), tuple(port.variable_name for port in block.output_ports))
                self.assertTrue(asset.is_file())
                self.assertEqual(
                    hashlib.sha256(asset.read_bytes()).hexdigest(),
                    block.fmu_sha256,
                )
                self.assertIs(InterfaceType.CO_SIMULATION, block.execution_interface)

    def test_sum_and_product_execute_through_the_existing_public_backend(self) -> None:
        backend = create_backend()
        for block_id, expected in (("farcel.math.sum", 5.0), ("farcel.math.product", 6.0)):
            with self.subTest(block_id=block_id):
                block = self.catalog.get_block(block_id)
                with resources.as_file(self.assets.joinpath(block.fmu_asset)) as fmu_path:
                    result = backend.run_fmu(
                        fmu_path,
                        SimulationConfig(
                            stop_time=0.02,
                            communication_step=0.01,
                            initial_inputs={"u1": 2.0, "u2": 3.0},
                            selected_outputs=("y",),
                        ),
                    )

                self.assertEqual((expected, expected, expected), result.outputs["y"])

    def test_graph_routes_two_constant_outputs_to_sum_and_product(self) -> None:
        factory = StandardBlockFactory(self.catalog)
        backend = create_backend()

        for block_id, node_id, expected in (
            ("farcel.math.sum", "sum", 5.0),
            ("farcel.math.product", "product", 6.0),
        ):
            with self.subTest(block_id=block_id):
                left = factory.create_model_node(
                    "farcel.sources.constant",
                    "left",
                    parameter_overrides={"value": 2.0},
                )
                right = factory.create_model_node(
                    "farcel.sources.constant",
                    "right",
                    parameter_overrides={"value": 3.0},
                )
                operation = factory.create_model_node(block_id, node_id)
                operation = replace(
                    operation,
                    config=replace(operation.config, selected_outputs=("y",)),
                )
                graph = SimulationGraph(
                    nodes=(left, right, operation),
                    connections=(
                        Connection(PortReference("left", "y"), PortReference(node_id, "u1")),
                        Connection(PortReference("right", "y"), PortReference(node_id, "u2")),
                    ),
                )
                config = GraphSimulationConfig(stop_time=0.02, communication_step=0.01)

                self.assertTrue(backend.validate_graph(graph, config).is_valid)
                result = backend.run_graph(graph, config)

                self.assertIs(SimulationState.COMPLETED, result.completion_state)
                self.assertEqual((0.0, 0.01, 0.02), result.timestamps)
                # explicit-Jacobi routes both Constant snapshots together. The
                # operation starts at zero and observes both inputs at 0.01.
                self.assertEqual((0.0, expected, expected), result.node_outputs[node_id]["y"])


if __name__ == "__main__":
    unittest.main()
