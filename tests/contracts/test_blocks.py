from dataclasses import FrozenInstanceError
import unittest

from farcel.contracts import (
    BlockCategoryDescriptor,
    BlockDescriptor,
    BlockParameterDescriptor,
    BlockPortDescriptor,
    BlockPortDirection,
    BlockValueType,
    InterfaceType,
)


def _parameter(
    variable_name: str = "gain",
    *,
    display_order: int = 0,
    minimum: float | None = None,
    maximum: float | None = None,
) -> BlockParameterDescriptor:
    return BlockParameterDescriptor(
        variable_name=variable_name,
        display_name="增益",
        description="将输入信号乘以指定系数。",
        value_type=BlockValueType.REAL,
        default_value=1.0,
        unit=None,
        minimum=minimum,
        maximum=maximum,
        display_on_block=True,
        display_order=display_order,
    )


def _port(
    variable_name: str,
    direction: BlockPortDirection,
) -> BlockPortDescriptor:
    return BlockPortDescriptor(
        variable_name=variable_name,
        display_name="输入" if direction is BlockPortDirection.INPUT else "输出",
        description="标准模块端口。",
        direction=direction,
        value_type=BlockValueType.REAL,
        unit=None,
        order=0,
    )


def _descriptor(**overrides: object) -> BlockDescriptor:
    values: dict[str, object] = {
        "block_id": "farcel.math.gain",
        "block_version": "1.0.0",
        "display_name": "增益",
        "description": "将输入乘以指定增益。",
        "category_id": "math",
        "category_display_name": "数学运算",
        "fmu_asset": "math/gain/Gain.fmu",
        "fmu_sha256": "a" * 64,
        "execution_interface": InterfaceType.CO_SIMULATION,
        "parameters": (_parameter(),),
        "input_ports": (_port("u", BlockPortDirection.INPUT),),
        "output_ports": (_port("y", BlockPortDirection.OUTPUT),),
    }
    values.update(overrides)
    return BlockDescriptor(**values)  # type: ignore[arg-type]


class StandardBlockContractTests(unittest.TestCase):
    def test_all_descriptors_construct_with_chinese_display_metadata(self) -> None:
        category = BlockCategoryDescriptor("math", "数学运算", "基础数学模块。", 2)
        descriptor = _descriptor()

        self.assertEqual(category.display_name, "数学运算")
        self.assertEqual(descriptor.display_name, "增益")
        self.assertEqual(descriptor.parameters[0].display_name, "增益")
        self.assertEqual(descriptor.input_ports[0].display_name, "输入")
        self.assertEqual(descriptor.schema_version, "1.0")

    def test_descriptors_are_immutable(self) -> None:
        descriptor = _descriptor()

        with self.assertRaises(FrozenInstanceError):
            descriptor.block_id = "farcel.math.other"

    def test_enums_are_stable_string_values(self) -> None:
        self.assertEqual(BlockValueType.REAL.value, "real")
        self.assertEqual(BlockPortDirection.INPUT.value, "input")
        self.assertEqual(BlockPortDirection.OUTPUT.value, "output")

    def test_parameter_rejects_invalid_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "minimum"):
            _parameter(minimum=2.0, maximum=1.0)

    def test_parameter_and_port_reject_empty_variable_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "parameter variable_name"):
            _parameter(variable_name="")
        with self.assertRaisesRegex(ValueError, "port variable_name"):
            _port("", BlockPortDirection.INPUT)

    def test_descriptor_rejects_empty_identifiers(self) -> None:
        for field_name in ("schema_version", "block_id", "block_version"):
            with self.subTest(field_name=field_name), self.assertRaises(ValueError):
                _descriptor(**{field_name: ""})

    def test_descriptor_rejects_duplicate_parameter_variable_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "parameter variable_name"):
            _descriptor(parameters=(_parameter("gain"), _parameter("gain", display_order=1)))

    def test_descriptor_rejects_duplicate_display_orders(self) -> None:
        with self.assertRaisesRegex(ValueError, "display_order"):
            _descriptor(parameters=(_parameter("gain"), _parameter("bias")))

    def test_descriptor_rejects_duplicate_port_variable_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "port variable_name"):
            _descriptor(
                input_ports=(_port("signal", BlockPortDirection.INPUT),),
                output_ports=(_port("signal", BlockPortDirection.OUTPUT),),
            )

    def test_descriptor_rejects_wrong_port_collection_direction(self) -> None:
        with self.assertRaisesRegex(ValueError, "direction"):
            _descriptor(input_ports=(_port("u", BlockPortDirection.OUTPUT),))
