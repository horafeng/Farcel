from __future__ import annotations

import hashlib
from importlib import resources
import unittest

from farcel import create_backend
from farcel.application import StandardBlockCatalogLoader, StandardBlockFactory
from farcel.contracts import InterfaceType, ModelNode, SimulationConfig


class StandardFirstOrderFmuAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = StandardBlockCatalogLoader().load()
        self.assets = resources.files("farcel.standard_library").joinpath("assets")

    def test_catalog_loads_first_order_descriptor_and_packaged_asset(self) -> None:
        block = self.catalog.get_block("farcel.continuous.first_order")
        asset = self.assets.joinpath(block.fmu_asset)

        self.assertEqual("一阶惯性环节", block.display_name)
        self.assertEqual("continuous", block.category_id)
        self.assertEqual("continuous/first_order/FirstOrder.fmu", block.fmu_asset)
        self.assertEqual(
            ("gain", "time_constant", "initial_value"),
            tuple(parameter.variable_name for parameter in block.parameters),
        )
        self.assertTrue(all(parameter.display_on_block for parameter in block.parameters))
        self.assertEqual(("u",), tuple(port.variable_name for port in block.input_ports))
        self.assertEqual(("y",), tuple(port.variable_name for port in block.output_ports))
        self.assertIs(InterfaceType.CO_SIMULATION, block.execution_interface)
        self.assertTrue(asset.is_file())
        self.assertEqual(hashlib.sha256(asset.read_bytes()).hexdigest(), block.fmu_sha256)

    def test_runtime_converges_to_gain_times_input(self) -> None:
        block = self.catalog.get_block("farcel.continuous.first_order")
        with resources.as_file(self.assets.joinpath(block.fmu_asset)) as fmu_path:
            result = create_backend().run_fmu(
                fmu_path,
                SimulationConfig(
                    stop_time=5.0,
                    communication_step=0.05,
                    parameters={"gain": 2.0, "time_constant": 1.0},
                    initial_inputs={"u": 1.0},
                    selected_outputs=("y",),
                ),
            )

        self.assertAlmostEqual(2.0, result.outputs["y"][-1], delta=0.02)

    def test_initial_value_is_the_initial_output(self) -> None:
        block = self.catalog.get_block("farcel.continuous.first_order")
        with resources.as_file(self.assets.joinpath(block.fmu_asset)) as fmu_path:
            result = create_backend().run_fmu(
                fmu_path,
                SimulationConfig(
                    stop_time=0.1,
                    communication_step=0.05,
                    parameters={
                        "gain": 2.0,
                        "time_constant": 1.0,
                        "initial_value": 1.25,
                    },
                    initial_inputs={"u": 1.0},
                    selected_outputs=("y",),
                ),
            )

        self.assertEqual(1.25, result.outputs["y"][0])

    def test_factory_creates_an_existing_model_node(self) -> None:
        block = self.catalog.get_block("farcel.continuous.first_order")
        node = StandardBlockFactory(self.catalog).create_model_node(
            block.block_id,
            "first-order",
            parameter_overrides={"gain": 2.0, "time_constant": 1.0},
        )

        self.assertIsInstance(node, ModelNode)
        self.assertEqual("first-order", node.node_id)
        self.assertTrue(node.model_path.endswith("FirstOrder.fmu"))
        self.assertEqual({"gain": 2.0, "time_constant": 1.0}, node.config.parameters)
        self.assertIs(InterfaceType.CO_SIMULATION, node.config.execution_interface)


if __name__ == "__main__":
    unittest.main()
