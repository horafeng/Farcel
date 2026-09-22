import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from farcel.application import StandardBlockCatalogLoader
from farcel.contracts import BlockPortDirection, BlockValueType, InterfaceType


def _catalog_data(*, block_assets: list[str] | None = None) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "categories": [
            {
                "category_id": "math",
                "display_name": "数学运算",
                "description": "基础数学运算模块。",
                "order": 2,
            }
        ],
        "block_assets": block_assets or ["categories/math/gain/block.json"],
    }


def _block_data() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "block_id": "farcel.math.gain",
        "block_version": "1.0.0",
        "display_name": "增益",
        "description": "将输入信号乘以指定系数。",
        "category_id": "math",
        "category_display_name": "数学运算",
        "fmu_asset": "categories/math/gain/Gain.fmu",
        "fmu_sha256": "0" * 64,
        "execution_interface": "co_simulation",
        "parameters": [
            {
                "variable_name": "gain",
                "display_name": "增益",
                "description": "输入信号的乘法系数。",
                "value_type": "real",
                "default_value": 1.0,
                "unit": None,
                "minimum": None,
                "maximum": None,
                "display_on_block": True,
                "display_order": 0,
            }
        ],
        "input_ports": [
            {
                "variable_name": "u",
                "display_name": "输入",
                "description": "待放大的输入信号。",
                "direction": "input",
                "value_type": "real",
                "unit": None,
                "order": 0,
            }
        ],
        "output_ports": [
            {
                "variable_name": "y",
                "display_name": "输出",
                "description": "增益计算后的输出信号。",
                "direction": "output",
                "value_type": "real",
                "unit": None,
                "order": 0,
            }
        ],
    }


class StandardBlockCatalogLoaderTests(unittest.TestCase):
    def test_reads_packaged_block_json_into_catalog(self) -> None:
        catalog = StandardBlockCatalogLoader().load()

        categories = {category.category_id: category for category in catalog.list_categories()}
        self.assertEqual(categories["math"].display_name, "数学运算")
        block = catalog.get_block("farcel.math.gain")
        self.assertEqual(block.display_name, "增益")
        self.assertIs(block.execution_interface, InterfaceType.CO_SIMULATION)
        self.assertIs(block.parameters[0].value_type, BlockValueType.REAL)
        self.assertIs(block.input_ports[0].direction, BlockPortDirection.INPUT)

    def test_catalog_json_registers_category_before_block(self) -> None:
        catalog = StandardBlockCatalogLoader().load()

        self.assertEqual(
            tuple(category.category_id for category in catalog.list_categories()),
            ("sources", "math"),
        )
        self.assertEqual(
            tuple(block.category_id for block in catalog.list_blocks()),
            ("sources", "sources", "sources", "math", "math", "math", "math", "math"),
        )

    def test_rejects_unsupported_schema_version(self) -> None:
        catalog = _catalog_data()
        catalog["schema_version"] = "2.0"

        with self._patched_assets(catalog), self.assertRaisesRegex(ValueError, "schema_version"):
            StandardBlockCatalogLoader().load()

    def test_rejects_missing_block_field(self) -> None:
        block = _block_data()
        del block["display_name"]

        with self._patched_assets(_catalog_data(), block), self.assertRaisesRegex(
            ValueError, "display_name"
        ):
            StandardBlockCatalogLoader().load()

    def test_rejects_unknown_block_metadata_asset_path(self) -> None:
        with self._patched_assets(_catalog_data(block_assets=["categories/math/missing/block.json"])):
            with self.assertRaisesRegex(ValueError, "asset 不存在"):
                StandardBlockCatalogLoader().load()

    def _patched_assets(
        self,
        catalog: dict[str, object],
        block: dict[str, object] | None = None,
    ):
        temporary_directory = TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        assets = Path(temporary_directory.name) / "assets"
        block_path = assets / "categories" / "math" / "gain" / "block.json"
        block_path.parent.mkdir(parents=True)
        (assets / "catalog.json").write_text(
            json.dumps(catalog, ensure_ascii=False), encoding="utf-8"
        )
        if block is not None or catalog["block_assets"] == ["categories/math/gain/block.json"]:
            block_path.write_text(
                json.dumps(block if block is not None else _block_data(), ensure_ascii=False),
                encoding="utf-8",
            )
        return patch(
            "farcel.application.standard_block_loader.resources.files",
            return_value=Path(temporary_directory.name),
        )
