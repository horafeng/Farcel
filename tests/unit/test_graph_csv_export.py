import csv
import tempfile
import unittest
from pathlib import Path

from farcel.contracts import EngineError, ErrorCode, GraphSimulationResult, SimulationState
from farcel.infrastructure.export import CsvGraphResultExporter


def graph_result(
    node_outputs: dict[str, dict[str, tuple[object, ...]]],
    *,
    state: SimulationState = SimulationState.COMPLETED,
    timestamps: tuple[float, ...] = (0.0, 0.1, 0.2),
    final_time: float = 0.2,
) -> GraphSimulationResult:
    return GraphSimulationResult(
        0.0,
        1.0,
        0.1,
        len(timestamps) - 1,
        final_time,
        state,
        timestamps,
        node_outputs,
    )


def read_csv(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.reader(stream))


class CsvGraphResultExporterTests(unittest.TestCase):
    def test_writes_sorted_signal_columns_and_canonical_timestamps(self) -> None:
        result = graph_result(
            {
                "B": {"z": (30, 31, 32)},
                "A": {"z": (20, 21, 22), "a": (10, 11, 12)},
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "nested" / "graph.csv"
            report = CsvGraphResultExporter().export(result, destination)

            self.assertEqual(
                read_csv(destination),
                [
                    ["time", "node/A/a", "node/A/z", "node/B/z"],
                    ["0.0", "10", "20", "30"],
                    ["0.1", "11", "21", "31"],
                    ["0.2", "12", "22", "32"],
                ],
            )
            self.assertEqual(report.row_count, result.sample_count)
            self.assertEqual(Path(report.destination), destination.resolve())

    def test_semantically_equal_reversed_mappings_export_identical_bytes(self) -> None:
        first = graph_result(
            {"B": {"z": (3, 4, 5)}, "A": {"z": (2, 3, 4), "a": (1, 2, 3)}}
        )
        second = graph_result(
            {"A": {"a": (1, 2, 3), "z": (2, 3, 4)}, "B": {"z": (3, 4, 5)}}
        )
        with tempfile.TemporaryDirectory() as directory:
            first_path = Path(directory) / "first.csv"
            second_path = Path(directory) / "second.csv"
            exporter = CsvGraphResultExporter()

            exporter.export(first, first_path)
            exporter.export(second, second_path)

            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())

    def test_escapes_each_json_pointer_header_segment(self) -> None:
        result = graph_result({"plant/A~1": {"y/out~": (1, 2, 3)}})
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "escaped.csv"
            CsvGraphResultExporter().export(result, destination)

            self.assertEqual(
                read_csv(destination)[0],
                ["time", "node/plant~1A~01/y~1out~0"],
            )

    def test_expands_one_and_two_dimensional_arrays_in_row_major_order(self) -> None:
        result = graph_result(
            {
                "A": {
                    "vector": ((1, 2), (3, 4), (5, 6)),
                    "matrix": (
                        ((1, 2), (3, 4)),
                        ((5, 6), (7, 8)),
                        ((9, 10), (11, 12)),
                    ),
                }
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "arrays.csv"
            CsvGraphResultExporter().export(result, destination)

            self.assertEqual(
                read_csv(destination),
                [
                    [
                        "time",
                        "node/A/matrix[0,0]", "node/A/matrix[0,1]",
                        "node/A/matrix[1,0]", "node/A/matrix[1,1]",
                        "node/A/vector[0]", "node/A/vector[1]",
                    ],
                    ["0.0", "1", "2", "3", "4", "1", "2"],
                    ["0.1", "5", "6", "7", "8", "3", "4"],
                    ["0.2", "9", "10", "11", "12", "5", "6"],
                ],
            )

    def test_stopped_result_exports_only_existing_samples(self) -> None:
        result = graph_result(
            {"A": {"y": (1.0, 2.0)}},
            state=SimulationState.STOPPED,
            timestamps=(0.0, 0.1),
            final_time=0.1,
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "stopped.csv"
            report = CsvGraphResultExporter().export(result, destination)

            self.assertEqual(
                read_csv(destination),
                [["time", "node/A/y"], ["0.0", "1.0"], ["0.1", "2.0"]],
            )
            self.assertEqual(report.row_count, 2)

    def test_exports_timeline_when_node_outputs_are_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "time-only.data"
            CsvGraphResultExporter().export(graph_result({}), destination)

            self.assertEqual(
                read_csv(destination),
                [["time"], ["0.0"], ["0.1"], ["0.2"]],
            )

    def test_shape_change_and_destination_failure_are_stable_export_errors(self) -> None:
        malformed = graph_result({"A": {"y": ((1, 2), (3, 4, 5), (6, 7))}})
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "shape.csv"
            with self.assertRaises(EngineError) as raised:
                CsvGraphResultExporter().export(malformed, destination)

            self.assertIs(raised.exception.code, ErrorCode.EXPORT_ERROR)
            self.assertEqual(raised.exception.details["destination"], str(destination.resolve()))
            self.assertIn("shape changed", raised.exception.details["diagnostic"])

            with self.assertRaises(EngineError) as raised:
                CsvGraphResultExporter().export(graph_result({}), Path(directory))

        self.assertIs(raised.exception.code, ErrorCode.EXPORT_ERROR)
        self.assertEqual(raised.exception.details["destination"], str(Path(directory).resolve()))
        self.assertIn("diagnostic", raised.exception.details)


if __name__ == "__main__":
    unittest.main()
