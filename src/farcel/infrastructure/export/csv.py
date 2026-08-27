from __future__ import annotations

import csv
from pathlib import Path

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
                output_names = tuple(result.outputs)
                writer.writerow(("time", *output_names))
                for index, timestamp in enumerate(result.timestamps):
                    writer.writerow(
                        (
                            timestamp,
                            *(result.outputs[name][index] for name in output_names),
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
