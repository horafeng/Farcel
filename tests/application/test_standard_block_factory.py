from __future__ import annotations

from importlib import resources
from pathlib import Path
import unittest

from farcel.application import (
    StandardBlockCatalogLoader,
    StandardBlockFactory,
)
from farcel.contracts import InterfaceType, ModelNode, ModelNodeConfig


class StandardBlockFactoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = StandardBlockCatalogLoader().load()
        self.factory = StandardBlockFactory(self.catalog)

    def test_creates_constant_model_node(self) -> None:
        node = self.factory.create_model_node(
            "farcel.sources.constant",
            "constant-1",
            parameter_overrides={"value": 2.5},
        )

        self.assertIsInstance(node, ModelNode)
        self.assertEqual("constant-1", node.node_id)
        self.assertTrue(Path(node.model_path).is_file())
        self.assertEqual({"value": 2.5}, node.config.parameters)
        self.assertEqual(InterfaceType.CO_SIMULATION, node.config.execution_interface)

    def test_creates_gain_model_node(self) -> None:
        node = self.factory.create_model_node(
            "farcel.math.gain",
            "gain-1",
            parameter_overrides={"gain": 3.0},
        )

        self.assertIsInstance(node, ModelNode)
        self.assertEqual("gain-1", node.node_id)
        self.assertTrue(Path(node.model_path).is_file())
        self.assertEqual({"gain": 3.0}, node.config.parameters)

    def test_missing_block_id_raises_catalog_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "标准模块不存在"):
            self.factory.create_model_node("farcel.unknown", "unknown-1")

    def test_passes_the_packaged_fmu_asset_path_to_model_node(self) -> None:
        descriptor = self.catalog.get_block("farcel.sources.constant")
        node = self.factory.create_model_node(descriptor.block_id, "constant-1")
        expected_asset = resources.files("farcel.standard_library").joinpath(
            "assets",
            descriptor.fmu_asset,
        )

        self.assertEqual(
            Path(expected_asset).resolve(),
            Path(node.model_path).resolve(),
        )

    def test_existing_model_node_contract_behavior_is_unchanged(self) -> None:
        config = ModelNodeConfig(
            parameters={"gain": 4.0},
            execution_interface=InterfaceType.CO_SIMULATION,
        )
        node = ModelNode("manual-gain", "C:/models/Gain.fmu", config)

        self.assertEqual("manual-gain", node.node_id)
        self.assertEqual("C:/models/Gain.fmu", node.model_path)
        self.assertEqual({"gain": 4.0}, node.config.parameters)
        self.assertEqual(InterfaceType.CO_SIMULATION, node.config.execution_interface)


if __name__ == "__main__":
    unittest.main()
