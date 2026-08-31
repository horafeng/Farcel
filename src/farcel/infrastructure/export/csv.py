from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from farcel.contracts._arrays import array_indices, flatten_array, infer_array_shape
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import ExportReport, SimulationResult


class CsvResultExporter:
    """Write a canonical SimulationResult as UTF-8 CSV."""

    def export(
        self, result: SimulationResult, destination: Path
    ) -> ExportReport:
        destination = destination.expanduser().resolve()
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                columns = _csv_columns(result.outputs)
                writer.writerow(("time", *(column.header for column in columns)))
                for index, timestamp in enumerate(result.timestamps):
                    writer.writerow(
                        (
                            timestamp,
                            *(
                                _csv_value(result.outputs[column.name][index], column)
                                for column in columns
                            ),
                        )
                    )
        except Exception as exc:
            raise EngineError(
                ErrorCode.EXPORT_ERROR,
                "CSV 结果导出失败",
                {"destination": str(destination), "diagnostic": str(exc)},
            ) from None

        return ExportReport(
            destination=str(destination),
            row_count=len(result.timestamps),
        )


class _CsvColumn:
    def __init__(self, name: str, header: str, indices: tuple[int, ...] | None) -> None:
        self.name = name
        self.header = header
        self.indices = indices


def _csv_columns(outputs: dict[str, tuple[Any, ...]]) -> tuple[_CsvColumn, ...]:
    columns: list[_CsvColumn] = []
    for name, samples in outputs.items():
        shape = infer_array_shape(samples[0])
        if not shape:
            columns.append(_CsvColumn(name, name, None))
            continue
        for indices in array_indices(shape):
            suffix = ",".join(str(index) for index in indices)
            columns.append(_CsvColumn(name, f"{name}[{suffix}]", indices))
    return tuple(columns)


def _csv_value(value: Any, column: _CsvColumn) -> Any:
    if column.indices is None:
        return value
    shape = infer_array_shape(value)
    flat_values = flatten_array(value, shape)
    return flat_values[_flat_index(column.indices, shape)]


def _flat_index(indices: tuple[int, ...], shape: tuple[int, ...]) -> int:
    offset = 0
    for index, dimension in zip(indices, shape):
        offset = offset * dimension + index
    return offset
