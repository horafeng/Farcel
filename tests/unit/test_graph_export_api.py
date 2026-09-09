from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from farcel import create_backend
from farcel.application.engine import FarcelEngine
from farcel.contracts import (
    EngineError,
    ErrorCode,
    ExportReport,
    GraphResultExporter,
    GraphSimulationConfig,
    GraphSimulationResult,
    ModelNode,
    ProjectRunArtifact,
    ProjectRunRecord,
    SimulationCase,
    SimulationGraph,
    SimulationProject,
    SimulationState,
)
from farcel.infrastructure.project import LocalJsonProjectRunArtifactRepository


class _Importer:
    pass


class _GraphExporter:
    def __init__(self) -> None:
        self.calls: list[tuple[GraphSimulationResult, Path]] = []

    def export(self, result: GraphSimulationResult, destination: Path) -> ExportReport:
        self.calls.append((result, destination))
        return ExportReport(str(destination), result.sample_count)


class GraphExportApiTests(unittest.TestCase):
    def test_engine_delegates_to_injected_graph_exporter(self) -> None:
        exporter = _GraphExporter()
        engine = FarcelEngine(_Importer(), graph_result_exporter=exporter)
        result = _result()

        report = engine.export_graph_result(result, "nested/result.csv")

        self.assertEqual(exporter.calls, [(result, Path("nested/result.csv"))])
        self.assertEqual(report.row_count, result.sample_count)

    def test_engine_without_graph_exporter_is_not_implemented(self) -> None:
        with self.assertRaises(EngineError) as raised:
            FarcelEngine(_Importer()).export_graph_result(_result(), "result.csv")

        self.assertIs(raised.exception.code, ErrorCode.NOT_IMPLEMENTED)

    def test_graph_exporter_port_is_public(self) -> None:
        self.assertTrue(hasattr(GraphResultExporter, "export"))

    def test_default_backend_exports_a_canonical_graph_result(self) -> None:
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "graph.csv"
            report = create_backend().export_graph_result(_result(), destination)

            self.assertEqual(report.row_count, 3)
            self.assertEqual(
                destination.read_text(encoding="utf-8").splitlines()[0],
                "time,node/plant/x0",
            )

    def test_loaded_historical_artifact_uses_the_same_graph_exporter(self) -> None:
        result = _result()
        case = SimulationCase(
            "case-1",
            "Historical case",
            SimulationGraph(nodes=(ModelNode("plant", "models/plant.fmu"),)),
            GraphSimulationConfig(stop_time=0.02, communication_step=0.01),
        )
        artifact = ProjectRunArtifact("run-1", "project", case, (), result)
        record = ProjectRunRecord(
            "run-1",
            "case-1",
            result.completion_state,
            result.final_time,
            result.completed_steps,
            "results/run-1.json",
        )
        project = SimulationProject("project", "Project", run_history=(record,))

        with TemporaryDirectory() as directory:
            root = Path(directory) / "Project"
            LocalJsonProjectRunArtifactRepository().save(root, artifact)
            backend = create_backend()
            historical = backend.load_project_run(root, project, "run-1")
            current_csv = root / "current.csv"
            historical_csv = root / "historical.csv"

            backend.export_graph_result(result, current_csv)
            backend.export_graph_result(historical.result, historical_csv)

            self.assertEqual(current_csv.read_bytes(), historical_csv.read_bytes())


def _result() -> GraphSimulationResult:
    return GraphSimulationResult(
        0.0,
        0.02,
        0.01,
        2,
        0.02,
        SimulationState.COMPLETED,
        (0.0, 0.01, 0.02),
        {"plant": {"x0": (2.0, 1.0, 0.5)}},
    )


if __name__ == "__main__":
    unittest.main()
