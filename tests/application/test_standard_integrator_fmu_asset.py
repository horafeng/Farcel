from __future__ import annotations

import hashlib
from importlib import resources
import unittest

from farcel import create_backend
from farcel.application import StandardBlockCatalogLoader, StandardBlockFactory
from farcel.contracts import InterfaceType, ModelNode, SimulationConfig


class StandardIntegratorFmuAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = StandardBlockCatalogLoader().load()
        self.assets = resources.files("farcel.standard_library").joinpath("assets")

    def test_catalog_loads_integrator_descriptor_and_packaged_asset(self) -> None:
        block = self.catalog.get_block("farcel.continuous.integrator")
        asset = self.assets.joinpath(block.fmu_asset)

        self.assertEqual("积分器", block.display_name)
        self.assertEqual("continuous", block.category_id)
        self.assertEqual("continuous/integrator/Integrator.fmu", block.fmu_asset)
        self.assertEqual(("initial_value",), tuple(parameter.variable_name for parameter in block.parameters))
        self.assertTrue(block.parameters[0].display_on_block)
        self.assertEqual(("u",), tuple(port.variable_name for port in block.input_ports))
        self.assertEqual(("y",), tuple(port.variable_name for port in block.output_ports))
        self.assertIs(InterfaceType.CO_SIMULATION, block.execution_interface)
        self.assertTrue(asset.is_file())
        self.assertEqual(hashlib.sha256(asset.read_bytes()).hexdigest(), block.fmu_sha256)

    def test_runtime_integrates_a_constant_input_for_five_seconds(self) -> None:
        block = self.catalog.get_block("farcel.continuous.integrator")
        with resources.as_file(self.assets.joinpath(block.fmu_asset)) as fmu_path:
            result = create_backend().run_fmu(
                fmu_path,
                SimulationConfig(
                    stop_time=5.0,
                    communication_step=0.05,
                    parameters={"initial_value": 0.0},
                    initial_inputs={"u": 1.0},
                    selected_outputs=("y",),
                ),
            )

        self.assertAlmostEqual(5.0, result.outputs["y"][-1], delta=1e-9)

    def test_initial_value_is_the_initial_output(self) -> None:
        block = self.catalog.get_block("farcel.continuous.integrator")
        with resources.as_file(self.assets.joinpath(block.fmu_asset)) as fmu_path:
            result = create_backend().run_fmu(
                fmu_path,
                SimulationConfig(
                    stop_time=0.1,
                    communication_step=0.05,
                    parameters={"initial_value": 1.25},
                    initial_inputs={"u": 1.0},
                    selected_outputs=("y",),
                ),
            )

        self.assertEqual(1.25, result.outputs["y"][0])

    def test_factory_creates_an_existing_model_node(self) -> None:
        block = self.catalog.get_block("farcel.continuous.integrator")
        node = StandardBlockFactory(self.catalog).create_model_node(
            block.block_id,
            "integrator",
            parameter_overrides={"initial_value": 1.25},
        )

        self.assertIsInstance(node, ModelNode)
        self.assertEqual("integrator", node.node_id)
        self.assertTrue(node.model_path.endswith("Integrator.fmu"))
        self.assertEqual({"initial_value": 1.25}, node.config.parameters)
        self.assertIs(InterfaceType.CO_SIMULATION, node.config.execution_interface)


if __name__ == "__main__":
    unittest.main()
