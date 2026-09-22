import unittest

from farcel.application import StandardBlockCatalog
from farcel.contracts import (
    BlockCategoryDescriptor,
    BlockDescriptor,
    BlockParameterDescriptor,
    BlockPortDescriptor,
    BlockPortDirection,
    BlockValueType,
    InterfaceType,
)


def _math_category() -> BlockCategoryDescriptor:
    return BlockCategoryDescriptor(
        category_id="math",
        display_name="数学运算",
        description="基础数学运算模块。",
        order=2,
    )


def _gain_block(
    *,
    block_id: str = "farcel.math.gain",
    category_id: str = "math",
) -> BlockDescriptor:
    parameter = BlockParameterDescriptor(
        variable_name="gain",
        display_name="增益",
        description="将输入乘以指定系数。",
        value_type=BlockValueType.REAL,
        default_value=1.0,
        unit=None,
        minimum=None,
        maximum=None,
        display_on_block=True,
        display_order=0,
    )
    input_port = BlockPortDescriptor(
        variable_name="u",
        display_name="输入",
        description="输入信号。",
        direction=BlockPortDirection.INPUT,
        value_type=BlockValueType.REAL,
        unit=None,
        order=0,
    )
    output_port = BlockPortDescriptor(
        variable_name="y",
        display_name="输出",
        description="输出信号。",
        direction=BlockPortDirection.OUTPUT,
        value_type=BlockValueType.REAL,
        unit=None,
        order=0,
    )
    return BlockDescriptor(
        block_id=block_id,
        block_version="1.0.0",
        display_name="增益",
        description="将输入乘以指定增益。",
        category_id=category_id,
        category_display_name="数学运算",
        fmu_asset="test-fixture/Gain.fmu",
        fmu_sha256="a" * 64,
        execution_interface=InterfaceType.CO_SIMULATION,
        parameters=(parameter,),
        input_ports=(input_port,),
        output_ports=(output_port,),
    )


class StandardBlockCatalogTests(unittest.TestCase):
    def test_registers_and_lists_category(self) -> None:
        catalog = StandardBlockCatalog()
        category = _math_category()

        catalog.register_category(category)

        self.assertEqual(catalog.list_categories(), (category,))

    def test_registers_lists_and_gets_block(self) -> None:
        catalog = StandardBlockCatalog()
        category = _math_category()
        block = _gain_block()
        catalog.register_category(category)

        catalog.register_block(block)

        self.assertEqual(catalog.list_blocks(), (block,))
        self.assertIs(catalog.get_block(block.block_id), block)

    def test_rejects_duplicate_category_id(self) -> None:
        catalog = StandardBlockCatalog()
        catalog.register_category(_math_category())

        with self.assertRaisesRegex(ValueError, "category_id"):
            catalog.register_category(_math_category())

    def test_rejects_duplicate_block_id(self) -> None:
        catalog = StandardBlockCatalog()
        catalog.register_category(_math_category())
        catalog.register_block(_gain_block())

        with self.assertRaisesRegex(ValueError, "block_id"):
            catalog.register_block(_gain_block())

    def test_rejects_block_for_unknown_category(self) -> None:
        catalog = StandardBlockCatalog()

        with self.assertRaisesRegex(ValueError, "category_id"):
            catalog.register_block(_gain_block(category_id="unknown"))

    def test_reports_missing_block_id(self) -> None:
        catalog = StandardBlockCatalog()

        with self.assertRaisesRegex(ValueError, "不存在"):
            catalog.get_block("farcel.math.missing")

    def test_listing_returns_immutable_tuples(self) -> None:
        catalog = StandardBlockCatalog()
        category = _math_category()
        catalog.register_category(category)
        catalog.register_block(_gain_block())

        self.assertIsInstance(catalog.list_categories(), tuple)
        self.assertIsInstance(catalog.list_blocks(), tuple)
        with self.assertRaises(AttributeError):
            catalog.list_blocks().append(_gain_block(block_id="farcel.math.other"))
