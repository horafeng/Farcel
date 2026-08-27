import csv
import tempfile
import unittest
from pathlib import Path

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import SimulationResult, SimulationState
from farcel.infrastructure.export import CsvResultExporter


def result_with(outputs: dict[str, tuple[object, ...]]) -> SimulationResult:
    return SimulationResult(
        fmu_path="example.fmu",
        start_time=0.0,
        stop_time=0.2,
        step_size=0.1,
        completed_steps=2,
        final_time=0.2,
        completion_state=SimulationState.COMPLETED,
        timestamps=(0.0, 0.1, 0.2),
        outputs=outputs,
    )


def read_csv(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.reader(stream))


class CsvResultExporterTests(unittest.TestCase):
    def test_writes_time_and_outputs_in_selection_order_and_creates_parent(self) -> None:
        result = result_with(
            {
                "y": (10, 11, 12),
                "x": (1.5, 1.6, 1.7),
                "enabled": (True, False, True),
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "nested" / "result.csv"
            report = CsvResultExporter().export(result, destination)

            self.assertEqual(
                read_csv(destination),
                [
                    ["time", "y", "x", "enabled"],
                    ["0.0", "10", "1.5", "True"],
                    ["0.1", "11", "1.6", "False"],
                    ["0.2", "12", "1.7", "True"],
                ],
            )
            self.assertEqual(report.row_count, 3)
            self.assertEqual(Path(report.destination), destination.resolve())

    def test_overwrites_exact_destination_without_adding_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "result.data"
            destination.write_text("stale data", encoding="utf-8")

            CsvResultExporter().export(result_with({}), destination)

            self.assertEqual(destination.name, "result.data")
            self.assertEqual(read_csv(destination)[0], ["time"])
            self.assertNotIn("stale data", destination.read_text(encoding="utf-8"))

    def test_exports_timeline_when_outputs_are_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "time-only.csv"
            CsvResultExporter().export(result_with({}), destination)

            self.assertEqual(
                read_csv(destination),
                [["time"], ["0.0"], ["0.1"], ["0.2"]],
            )

    def test_string_values_round_trip_with_standard_csv_reader(self) -> None:
        strings = ("中文,comma", 'quote "value"', "line one\nline two")
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "strings.csv"
            CsvResultExporter().export(result_with({"label": strings}), destination)

            rows = read_csv(destination)
            self.assertEqual([row[1] for row in rows[1:]], list(strings))

    def test_file_error_is_mapped_to_stable_engine_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            with self.assertRaises(EngineError) as raised:
                CsvResultExporter().export(result_with({}), destination)

        self.assertEqual(raised.exception.code, ErrorCode.EXPORT_ERROR)
        self.assertEqual(raised.exception.details["destination"], str(destination.resolve()))
        self.assertEqual(str(raised.exception), "EXPORT_ERROR: CSV 结果导出失败")


if __name__ == "__main__":
    unittest.main()
