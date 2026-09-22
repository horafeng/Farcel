"""Packaged JSON metadata loader for Farcel standard blocks."""

from __future__ import annotations

from collections.abc import Mapping
import json
from importlib import resources

from farcel.application.standard_library import StandardBlockCatalog
from farcel.contracts.blocks import (
    BlockCategoryDescriptor,
    BlockDescriptor,
    BlockParameterDescriptor,
    BlockPortDescriptor,
    BlockPortDirection,
    BlockValueType,
)
from farcel.contracts.models import InterfaceType


class StandardBlockCatalogLoader:
    """Loads packaged standard-block JSON metadata into an application catalog."""

    def __init__(
        self,
        package: str = "farcel.standard_library",
        assets_directory: str = "assets",
    ) -> None:
        self._package = package
        self._assets_directory = assets_directory

    def load(self) -> StandardBlockCatalog:
        """Read packaged metadata and return a newly populated catalog."""
        assets_root = resources.files(self._package).joinpath(self._assets_directory)
        catalog_data = self._read_object(assets_root.joinpath("catalog.json"), "catalog.json")
        self._validate_schema_version(catalog_data, "catalog.json")

        catalog = StandardBlockCatalog()
        for category_data in self._required_list(catalog_data, "categories", "catalog.json"):
            catalog.register_category(self._category_from_json(category_data))

        for asset_path in self._required_list(catalog_data, "block_assets", "catalog.json"):
            if not isinstance(asset_path, str):
                raise ValueError("catalog.json block_assets 必须包含字符串路径")
            block_resource = assets_root.joinpath(asset_path)
            if not block_resource.is_file():
                raise ValueError(f"标准模块 metadata asset 不存在: {asset_path}")
            catalog.register_block(
                self._block_from_json(self._read_object(block_resource, asset_path), asset_path)
            )

        return catalog

    @classmethod
    def _category_from_json(cls, data: object) -> BlockCategoryDescriptor:
        mapping = cls._as_mapping(data, "category")
        return BlockCategoryDescriptor(
            category_id=cls._required_string(mapping, "category_id", "category"),
            display_name=cls._required_string(mapping, "display_name", "category"),
            description=cls._required_string(mapping, "description", "category"),
            order=cls._required_integer(mapping, "order", "category"),
        )

    @classmethod
    def _block_from_json(cls, data: object, source: str) -> BlockDescriptor:
        mapping = cls._as_mapping(data, source)
        cls._validate_schema_version(mapping, source)
        return BlockDescriptor(
            schema_version=cls._required_string(mapping, "schema_version", source),
            block_id=cls._required_string(mapping, "block_id", source),
            block_version=cls._required_string(mapping, "block_version", source),
            display_name=cls._required_string(mapping, "display_name", source),
            description=cls._required_string(mapping, "description", source),
            category_id=cls._required_string(mapping, "category_id", source),
            category_display_name=cls._required_string(
                mapping, "category_display_name", source
            ),
            fmu_asset=cls._required_string(mapping, "fmu_asset", source),
            fmu_sha256=cls._required_string(mapping, "fmu_sha256", source),
            execution_interface=cls._interface_type(mapping, source),
            parameters=tuple(
                cls._parameter_from_json(item, source)
                for item in cls._required_list(mapping, "parameters", source)
            ),
            input_ports=tuple(
                cls._port_from_json(item, source)
                for item in cls._required_list(mapping, "input_ports", source)
            ),
            output_ports=tuple(
                cls._port_from_json(item, source)
                for item in cls._required_list(mapping, "output_ports", source)
            ),
        )

    @classmethod
    def _parameter_from_json(
        cls,
        data: object,
        source: str,
    ) -> BlockParameterDescriptor:
        mapping = cls._as_mapping(data, f"{source} parameter")
        return BlockParameterDescriptor(
            variable_name=cls._required_string(mapping, "variable_name", source),
            display_name=cls._required_string(mapping, "display_name", source),
            description=cls._required_string(mapping, "description", source),
            value_type=cls._value_type(mapping, source),
            default_value=cls._required_value(mapping, "default_value", source),
            unit=cls._optional_string(mapping, "unit", source),
            minimum=cls._optional_number(mapping, "minimum", source),
            maximum=cls._optional_number(mapping, "maximum", source),
            display_on_block=cls._required_boolean(mapping, "display_on_block", source),
            display_order=cls._required_integer(mapping, "display_order", source),
        )

    @classmethod
    def _port_from_json(cls, data: object, source: str) -> BlockPortDescriptor:
        mapping = cls._as_mapping(data, f"{source} port")
        return BlockPortDescriptor(
            variable_name=cls._required_string(mapping, "variable_name", source),
            display_name=cls._required_string(mapping, "display_name", source),
            description=cls._required_string(mapping, "description", source),
            direction=cls._port_direction(mapping, source),
            value_type=cls._value_type(mapping, source),
            unit=cls._optional_string(mapping, "unit", source),
            order=cls._required_integer(mapping, "order", source),
        )

    @staticmethod
    def _read_object(resource: resources.abc.Traversable, source: str) -> Mapping[str, object]:
        try:
            data = json.loads(resource.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ValueError(f"标准模块 metadata asset 不存在: {source}") from None
        except json.JSONDecodeError as error:
            raise ValueError(f"{source} 不是有效 JSON: {error.msg}") from error
        return StandardBlockCatalogLoader._as_mapping(data, source)

    @staticmethod
    def _as_mapping(data: object, source: str) -> Mapping[str, object]:
        if not isinstance(data, Mapping):
            raise ValueError(f"{source} 必须是 JSON object")
        return data

    @staticmethod
    def _required_value(mapping: Mapping[str, object], field: str, source: str) -> object:
        if field not in mapping:
            raise ValueError(f"{source} 缺少字段: {field}")
        return mapping[field]

    @classmethod
    def _required_string(cls, mapping: Mapping[str, object], field: str, source: str) -> str:
        value = cls._required_value(mapping, field, source)
        if not isinstance(value, str):
            raise ValueError(f"{source} 字段必须是字符串: {field}")
        return value

    @classmethod
    def _optional_string(cls, mapping: Mapping[str, object], field: str, source: str) -> str | None:
        value = cls._required_value(mapping, field, source)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{source} 字段必须是字符串或 null: {field}")
        return value

    @classmethod
    def _required_integer(cls, mapping: Mapping[str, object], field: str, source: str) -> int:
        value = cls._required_value(mapping, field, source)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{source} 字段必须是整数: {field}")
        return value

    @classmethod
    def _required_boolean(cls, mapping: Mapping[str, object], field: str, source: str) -> bool:
        value = cls._required_value(mapping, field, source)
        if not isinstance(value, bool):
            raise ValueError(f"{source} 字段必须是布尔值: {field}")
        return value

    @classmethod
    def _optional_number(cls, mapping: Mapping[str, object], field: str, source: str) -> float | None:
        value = cls._required_value(mapping, field, source)
        if value is None:
            return None
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{source} 字段必须是数值或 null: {field}")
        return float(value)

    @classmethod
    def _required_list(cls, mapping: Mapping[str, object], field: str, source: str) -> list[object]:
        value = cls._required_value(mapping, field, source)
        if not isinstance(value, list):
            raise ValueError(f"{source} 字段必须是数组: {field}")
        return value

    @classmethod
    def _validate_schema_version(cls, mapping: Mapping[str, object], source: str) -> None:
        if cls._required_string(mapping, "schema_version", source) != "1.0":
            raise ValueError(f"{source} 不支持的 schema_version")

    @classmethod
    def _interface_type(cls, mapping: Mapping[str, object], source: str) -> InterfaceType:
        value = cls._required_string(mapping, "execution_interface", source)
        try:
            return InterfaceType(value)
        except ValueError:
            raise ValueError(f"{source} execution_interface 无效: {value}") from None

    @classmethod
    def _value_type(cls, mapping: Mapping[str, object], source: str) -> BlockValueType:
        value = cls._required_string(mapping, "value_type", source)
        try:
            return BlockValueType(value)
        except ValueError:
            raise ValueError(f"{source} value_type 无效: {value}") from None

    @classmethod
    def _port_direction(cls, mapping: Mapping[str, object], source: str) -> BlockPortDirection:
        value = cls._required_string(mapping, "direction", source)
        try:
            return BlockPortDirection(value)
        except ValueError:
            raise ValueError(f"{source} direction 无效: {value}") from None
