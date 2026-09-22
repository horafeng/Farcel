from __future__ import annotations

import hashlib
from importlib import resources
import json
from pathlib import Path
import unittest

from farcel.application import StandardBlockCatalogLoader, StandardBlockFactory
from farcel.contracts import ModelNode


_OBVIOUS_PLACEHOLDERS = frozenset({"todo", "tbd", "placeholder", "unknown", "n/a"})


class StandardBlockLibraryIntegrationGateTests(unittest.TestCase):
    """Validate the packaged standard-library inventory as one integration surface."""

    def setUp(self) -> None:
        self.catalog = StandardBlockCatalogLoader().load()
        self.assets = resources.files("farcel.standard_library").joinpath("assets")

    def test_catalog_inventory_metadata_and_packaged_assets_are_consistent(self) -> None:
        categories = self.catalog.list_categories()
        blocks = self.catalog.list_blocks()

        catalog_resource = self.assets.joinpath("catalog.json")
        self.assertTrue(catalog_resource.is_file())
        manifest = json.loads(catalog_resource.read_text(encoding="utf-8"))
        for metadata_asset in manifest["block_assets"]:
            with self.subTest(metadata_asset=metadata_asset):
                self.assertTrue(self.assets.joinpath(metadata_asset).is_file())
        self.assertEqual(len(categories), len({category.category_id for category in categories}))
        self.assertEqual(len(blocks), len({block.block_id for block in blocks}))

        categories_by_id = {category.category_id: category for category in categories}
        for category in categories:
            with self.subTest(category=category.category_id):
                self._assert_user_visible_text(category.display_name)
                self._assert_user_visible_text(category.description)

        for block in blocks:
            with self.subTest(block=block.block_id):
                self.assertIn(block.category_id, categories_by_id)
                self.assertEqual(
                    categories_by_id[block.category_id].display_name,
                    block.category_display_name,
                )
                self._assert_user_visible_text(block.display_name)
                self._assert_user_visible_text(block.description)
                self._assert_user_visible_text(block.category_display_name)

                asset = self.assets.joinpath(block.fmu_asset)
                self.assertTrue(asset.is_file())
                self.assertEqual(
                    hashlib.sha256(asset.read_bytes()).hexdigest(), block.fmu_sha256
                )

                ports = (*block.input_ports, *block.output_ports)
                self.assertTrue(ports)
                self.assertEqual(
                    len(ports), len({port.variable_name for port in ports})
                )
                self.assertEqual(
                    len(block.parameters),
                    len({parameter.variable_name for parameter in block.parameters}),
                )

                for port in ports:
                    self._assert_user_visible_text(port.display_name)
                    self._assert_user_visible_text(port.description)
                for parameter in block.parameters:
                    self._assert_user_visible_text(parameter.display_name)
                    self._assert_user_visible_text(parameter.description)

    def test_factory_creates_unique_nodes_for_the_entire_catalog(self) -> None:
        factory = StandardBlockFactory(self.catalog)
        blocks = self.catalog.list_blocks()
        nodes = tuple(
            factory.create_model_node(
                block.block_id,
                f"integration-gate-{index}",
                parameter_overrides={
                    parameter.variable_name: parameter.default_value
                    for parameter in block.parameters
                },
            )
            for index, block in enumerate(blocks)
        )

        self.assertEqual(len(nodes), len({node.node_id for node in nodes}))
        for block, node in zip(blocks, nodes):
            with self.subTest(block=block.block_id):
                self.assertIsInstance(node, ModelNode)
                self.assertTrue(Path(node.model_path).is_file())
                self.assertEqual(block.execution_interface, node.config.execution_interface)
                self.assertEqual(
                    {
                        parameter.variable_name: parameter.default_value
                        for parameter in block.parameters
                    },
                    node.config.parameters,
                )

    @staticmethod
    def _assert_user_visible_text(value: str) -> None:
        normalized = value.strip()
        if not normalized:
            raise AssertionError("用户可见 metadata 文本不能为空")
        if normalized.casefold() in _OBVIOUS_PLACEHOLDERS:
            raise AssertionError(f"用户可见 metadata 仍是占位文本: {value}")
