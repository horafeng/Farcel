from __future__ import annotations

from collections.abc import Iterable, Sequence
from itertools import product
from typing import Any


class ArrayShapeError(ValueError):
    """Raised when a public nested array value does not match its FMI shape."""


class EffectiveShapeError(ValueError):
    """Raised when structural values cannot resolve an FMI array shape."""


def array_size(shape: tuple[int, ...]) -> int:
    size = 1
    for dimension in shape:
        size *= dimension
    return size


def resolve_effective_shape(
    default_shape: tuple[int, ...],
    dimension_value_references: tuple[int | None, ...],
    structural_values_by_vr: dict[int, Any],
) -> tuple[int, ...]:
    """Resolve one run's array shape without changing static metadata."""

    if not default_shape:
        return ()
    if not dimension_value_references:
        return default_shape
    if len(default_shape) != len(dimension_value_references):
        raise EffectiveShapeError("dimension dependency count does not match shape")

    dimensions: list[int] = []
    for default_dimension, value_reference in zip(
        default_shape, dimension_value_references
    ):
        value = (
            default_dimension
            if value_reference is None
            else structural_values_by_vr.get(value_reference)
        )
        if value_reference is not None and value_reference not in structural_values_by_vr:
            raise EffectiveShapeError("referenced structural value is unavailable")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise EffectiveShapeError("array dimensions must be non-negative integers")
        dimensions.append(value)
    return tuple(dimensions)


def flatten_array(value: Any, shape: tuple[int, ...]) -> tuple[Any, ...]:
    """Validate a nested sequence against shape and return row-major values."""

    flattened: list[Any] = []

    def visit(item: Any, dimensions: tuple[int, ...]) -> None:
        if not dimensions:
            flattened.append(item)
            return
        if not _is_array_sequence(item) or len(item) != dimensions[0]:
            raise ArrayShapeError("array value does not match the declared shape")
        for child in item:
            visit(child, dimensions[1:])

    visit(value, shape)
    return tuple(flattened)


def reshape_array(values: Iterable[Any], shape: tuple[int, ...]) -> Any:
    """Return a nested tuple in row-major order for the declared FMI shape."""

    flattened = tuple(values)
    if len(flattened) != array_size(shape):
        raise ArrayShapeError("array value count does not match the declared shape")
    position = 0

    def build(dimensions: tuple[int, ...]) -> Any:
        nonlocal position
        if not dimensions:
            value = flattened[position]
            position += 1
            return value
        return tuple(build(dimensions[1:]) for _ in range(dimensions[0]))

    return build(shape)


def infer_array_shape(value: Any) -> tuple[int, ...]:
    """Infer a rectangular nested-sequence shape for result export only."""

    if not _is_array_sequence(value):
        return ()
    child_shapes = tuple(infer_array_shape(item) for item in value)
    if child_shapes and any(shape != child_shapes[0] for shape in child_shapes[1:]):
        raise ArrayShapeError("array result is not rectangular")
    return (len(value),) + (child_shapes[0] if child_shapes else ())


def array_indices(shape: tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
    return tuple(product(*(range(dimension) for dimension in shape)))


def _is_array_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
