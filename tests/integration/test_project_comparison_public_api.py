from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from farcel import create_backend
from farcel.application.project_run_persistence import ProjectRunPersistenceService
from farcel.contracts import (
    GraphSimulationConfig,
    GraphSimulationResult,
    ModelAsset,
    ModelNode,
    ProjectRunArtifact,
    SimulationCase,
    SimulationGraph,
    SimulationProject,
    SimulationState,
)
from farcel.infrastructure.project import (
    LocalJsonProjectRepository,
    LocalJsonProjectRunArtifactRepository,
)


class ProjectComparisonPublicApiIntegrationTests(unittest.TestCase):
    def test_loaded_artifacts_compare_in_caller_order_without_current_project_validation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "Project"
            project = _project()
            repository = LocalJsonProjectRepository()
            artifacts = LocalJsonProjectRunArtifactRepository()
            identifiers = iter(("run-A", "run-B"))
            persistence = ProjectRunPersistenceService(repository, artifacts, lambda: next(identifiers))
            after_a, record_a = persistence.persist_run(root, project, "case-A", _result((0.0, 0.1, 0.2), (1.0, 2.0, 3.0)))
            after_b, record_b = persistence.persist_run(root, after_a, "case-B", _result((0.0, 0.05, 0.1, 0.15, 0.2), (10.0, 20.0, 30.0, 40.0, 50.0)))
            backend = create_backend()
            reopened = backend.open_project(root)

            artifact_a = backend.load_project_run(root, reopened, record_a.run_id)
            artifact_b = backend.load_project_run(root, reopened, record_b.run_id)
            comparison = backend.compare_project_runs((artifact_b, artifact_a))

            self.assertEqual(tuple(source.run_id for source in comparison.sources), ("run-B", "run-A"))
            self.assertEqual(tuple(source.case_snapshot.name for source in comparison.sources), ("High gain", "Baseline"))
            series = comparison.signals[0].series
            self.assertEqual(tuple(item.run_id for item in series), ("run-B", "run-A"))
            self.assertEqual(series[0].timestamps, artifact_b.result.timestamps)
            self.assertEqual(series[1].timestamps, artifact_a.result.timestamps)
            self.assertEqual(series[0].statistics.mean, 30.0)
            self.assertEqual(series[1].statistics.mean, 2.0)
            self.assertEqual(reopened, after_b)


def _project() -> SimulationProject:
    asset = ModelAsset("asset", "Historical only", "models/missing.fmu", "a" * 64)
    cases = tuple(
        SimulationCase(
            case_id,
            name,
            SimulationGraph(nodes=(ModelNode("plant", "models/missing.fmu"),)),
            GraphSimulationConfig(stop_time=0.2, communication_step=0.1),
        )
        for case_id, name in (("case-A", "Baseline"), ("case-B", "High gain"))
    )
    return SimulationProject("project", "Project", (asset,), cases)


def _result(timestamps: tuple[float, ...], samples: tuple[float, ...]) -> GraphSimulationResult:
    return GraphSimulationResult(
        timestamps[0],
        0.2,
        0.1,
        len(timestamps) - 1,
        timestamps[-1],
        SimulationState.COMPLETED,
        timestamps,
        {"plant": {"x": samples}},
    )
