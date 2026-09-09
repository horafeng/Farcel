from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from farcel.contracts._arrays import array_indices, flatten_array, infer_array_shape
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.graph import GraphSimulationResult
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


class CsvGraphResultExporter:
    """Write a canonical GraphSimulationResult as deterministic UTF-8 CSV."""

    def export(
        self, result: GraphSimulationResult, destination: Path
    ) -> ExportReport:
        resolved_destination = destination
        try:
            resolved_destination = destination.expanduser().resolve()
            columns = _graph_csv_columns(result)
            resolved_destination.parent.mkdir(parents=True, exist_ok=True)
            with resolved_destination.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(("time", *(column.header for column in columns)))
                for index, timestamp in enumerate(result.timestamps):
                    writer.writerow(
                        (
                            timestamp,
                            *(
                                _graph_csv_value(
                                    result.node_outputs[column.node_id][
                                        column.variable_name
                                    ][index],
                                    column,
                                )
                                for column in columns
                            ),
                        )
                    )
        except Exception as exc:
            raise EngineError(
                ErrorCode.EXPORT_ERROR,
                "Graph CSV 结果导出失败",
                {
                    "destination": str(resolved_destination),
                    "diagnostic": str(exc),
                },
            ) from None

        return ExportReport(
            destination=str(resolved_destination),
            row_count=result.sample_count,
        )


class _CsvColumn:
    def __init__(self, name: str, header: str, indices: tuple[int, ...] | None) -> None:
        self.name = name
        self.header = header
        self.indices = indices


class _GraphCsvColumn:
    def __init__(
        self,
        node_id: str,
        variable_name: str,
        header: str,
        indices: tuple[int, ...] | None,
        shape: tuple[int, ...],
    ) -> None:
        self.node_id = node_id
        self.variable_name = variable_name
        self.header = header
        self.indices = indices
        self.shape = shape


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


def _graph_csv_columns(result: GraphSimulationResult) -> tuple[_GraphCsvColumn, ...]:
    columns: list[_GraphCsvColumn] = []
    for node_id in sorted(result.node_outputs):
        outputs = result.node_outputs[node_id]
        for variable_name in sorted(outputs):
            samples = outputs[variable_name]
            shape = infer_array_shape(samples[0])
            header = _graph_signal_header(node_id, variable_name)
            if not shape:
                columns.append(
                    _GraphCsvColumn(node_id, variable_name, header, None, shape)
                )
                continue
            for indices in array_indices(shape):
                suffix = ",".join(str(index) for index in indices)
                columns.append(
                    _GraphCsvColumn(
                        node_id,
                        variable_name,
                        f"{header}[{suffix}]",
                        indices,
                        shape,
                    )
                )
    return tuple(columns)


def _graph_signal_header(node_id: str, variable_name: str) -> str:
    return f"node/{_json_pointer_segment(node_id)}/{_json_pointer_segment(variable_name)}"


def _json_pointer_segment(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _graph_csv_value(value: Any, column: _GraphCsvColumn) -> Any:
    if column.indices is None:
        return value
    shape = infer_array_shape(value)
    if shape != column.shape:
        raise ValueError("array result shape changed between samples")
    flat_values = flatten_array(value, shape)
    return flat_values[_flat_index(column.indices, shape)]


def _flat_index(indices: tuple[int, ...], shape: tuple[int, ...]) -> int:
    offset = 0
    for index, dimension in zip(indices, shape):
        offset = offset * dimension + index
    return offset
