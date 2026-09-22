"""Implementation-independent contracts for Farcel standard blocks."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from farcel.contracts.models import InterfaceType


class BlockValueType(str, Enum):
    """Value types supported by standard-block descriptors."""

    REAL = "real"


class BlockPortDirection(str, Enum):
    """The declarative direction of a standard-block port."""

    INPUT = "input"
    OUTPUT = "output"


@dataclass(frozen=True, slots=True)
class BlockCategoryDescriptor:
    """User-visible metadata for one standard-block category."""

    category_id: str
    display_name: str
    description: str
    order: int


@dataclass(frozen=True, slots=True)
class BlockParameterDescriptor:
    """One FMI-facing standard-block parameter and its display metadata."""

    variable_name: str
    display_name: str
    description: str
    value_type: BlockValueType
    default_value: object
    unit: str | None
    minimum: float | None
    maximum: float | None
    display_on_block: bool
    display_order: int

    def __post_init__(self) -> None:
        if not self.variable_name:
            raise ValueError("parameter variable_name 不能为空")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("parameter minimum 不能大于 maximum")


@dataclass(frozen=True, slots=True)
class BlockPortDescriptor:
    """One FMI-facing standard-block input or output port."""

    variable_name: str
    display_name: str
    description: str
    direction: BlockPortDirection
    value_type: BlockValueType
    unit: str | None
    order: int

    def __post_init__(self) -> None:
        if not self.variable_name:
            raise ValueError("port variable_name 不能为空")


@dataclass(frozen=True, slots=True)
class BlockDescriptor:
    """Complete declarative metadata for one packaged standard-block FMU."""

    schema_version: str = field(default="1.0", kw_only=True)
    block_id: str
    block_version: str
    display_name: str
    description: str
    category_id: str
    category_display_name: str
    fmu_asset: str
    fmu_sha256: str
    execution_interface: InterfaceType
    parameters: tuple[BlockParameterDescriptor, ...] = ()
    input_ports: tuple[BlockPortDescriptor, ...] = ()
    output_ports: tuple[BlockPortDescriptor, ...] = ()

    def __post_init__(self) -> None:
        if not self.schema_version:
            raise ValueError("schema_version 不能为空")
        if not self.block_id:
            raise ValueError("block_id 不能为空")
        if not self.block_version:
            raise ValueError("block_version 不能为空")
        if not isinstance(self.parameters, tuple):
            raise TypeError("parameters 必须是 tuple")
        if not isinstance(self.input_ports, tuple):
            raise TypeError("input_ports 必须是 tuple")
        if not isinstance(self.output_ports, tuple):
            raise TypeError("output_ports 必须是 tuple")

        parameter_names = tuple(parameter.variable_name for parameter in self.parameters)
        if len(set(parameter_names)) != len(parameter_names):
            raise ValueError("parameter variable_name 不能重复")

        display_orders = tuple(parameter.display_order for parameter in self.parameters)
        if len(set(display_orders)) != len(display_orders):
            raise ValueError("parameter display_order 不能重复")

        self._validate_port_group(self.input_ports, BlockPortDirection.INPUT)
        self._validate_port_group(self.output_ports, BlockPortDirection.OUTPUT)
        port_names = tuple(
            port.variable_name for port in (*self.input_ports, *self.output_ports)
        )
        if len(set(port_names)) != len(port_names):
            raise ValueError("port variable_name 不能重复")

    @staticmethod
    def _validate_port_group(
        ports: tuple[BlockPortDescriptor, ...],
        expected_direction: BlockPortDirection,
    ) -> None:
        if any(port.direction is not expected_direction for port in ports):
            raise ValueError(
                f"{expected_direction.value} ports 必须使用对应的 direction"
            )
