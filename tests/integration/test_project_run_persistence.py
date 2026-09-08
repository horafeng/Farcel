import hashlib
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from farcel import create_backend
from farcel.application.project_run_persistence import ProjectRunPersistenceService
from farcel.application.project_service import ProjectService
from farcel.application.project_validation import ProjectValidator
from farcel.contracts import (
    EngineError,
    ErrorCode,
    GraphSimulationConfig,
    GraphSimulationResult,
    InterfaceType,
    ModelAsset,
    ModelNode,
    ModelNodeConfig,
    SimulationCase,
    SimulationGraph,
    SimulationProject,
    SimulationState,
)
from farcel.infrastructure.fmpy import FmpyImporter
from farcel.infrastructure.project import (
    LocalJsonProjectRepository,
    LocalJsonProjectRunArtifactRepository,
)


ROOT = Path(__file__).resolve().parents[2]
VAN_DER_POL = ROOT / "examples" / "fmus" / "VanDerPol.fmu"


@unittest.skipUnless(VAN_DER_POL.is_file(), "VanDerPol FMU is unavailable")
class ProjectRunPersistenceIntegrationTests(unittest.TestCase):
    def test_real_case_run_persists_reopens_and_relocates(self) -> None:
        with TemporaryDirectory() as directory:
            location_a = Path(directory) / "LocationA" / "Project"
            location_b = Path(directory) / "LocationB" / "Project"
            project = _project_with_van_der_pol(location_a)
            project_repository = LocalJsonProjectRepository()
            artifact_repository = LocalJsonProjectRunArtifactRepository()
            project_repository.save(location_a, project)
            backend = create_backend()
            result = ProjectService(
                ProjectValidator(FmpyImporter()), backend.run_graph
            ).run_case(location_a, project, "case-1")
            updated, record = ProjectRunPersistenceService(
                project_repository, artifact_repository, lambda: "run-integration"
            ).persist_run(location_a, project, "case-1", result)

            reopened = project_repository.load(location_a)
            artifact = artifact_repository.load(location_a, record.result_path)
            shutil.copytree(location_a, location_b)
            relocated_project = project_repository.load(location_b)
            relocated_artifact = artifact_repository.load(
                location_b, relocated_project.run_history[0].result_path
            )

        self.assertIs(result.completion_state, SimulationState.COMPLETED)
        self.assertEqual((result.completed_steps, result.final_time, result.sample_count), (2, 0.02, 3))
        self.assertIn("x0", result.node_outputs["plant"])
        self.assertEqual(updated.run_history, (record,))
        self.assertEqual((reopened.run_history[0].run_id, reopened.run_history[0].case_id), ("run-integration", "case-1"))
        self.assertEqual(reopened.run_history[0].result_path, "results/run-integration.json")
        self.assertEqual(artifact.case_snapshot.graph.nodes[0].model_path, "models/VanDerPol.fmu")
        self.assertEqual(artifact.result.final_time, 0.02)
        self.assertIn("x0", artifact.result.node_outputs["plant"])
        self.assertEqual(relocated_artifact.run_id, "run-integration")
        self.assertEqual(relocated_artifact.case_snapshot.graph.nodes[0].model_path, "models/VanDerPol.fmu")


class ProjectRunPersistenceFailureIntegrationTests(unittest.TestCase):
    def test_project_save_failure_leaves_loadable_orphan_and_original_project_json(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "Project"
            project = _project_without_runtime(root)
            project_repository = LocalJsonProjectRepository()
            artifact_repository = LocalJsonProjectRunArtifactRepository()
            project_repository.save(root, project)
            original_project_json = (root / "project.json").read_bytes()
            service = ProjectRunPersistenceService(
                _FailingProjectRepository(), artifact_repository, lambda: "run-failed"
            )

            with self.assertRaises(EngineError) as raised:
                service.persist_run(root, project, "case-1", _result())

            orphan = artifact_repository.load(root, "results/run-failed.json")
            current_project_json = (root / "project.json").read_bytes()

        self.assertIs(raised.exception.code, ErrorCode.PROJECT_IO_ERROR)
        self.assertEqual(current_project_json, original_project_json)
        self.assertEqual(orphan.run_id, "run-failed")
        self.assertEqual(raised.exception.details["orphan_result_path"], "results/run-failed.json")
        self.assertFalse(raised.exception.details["history_committed"])


class _FailingProjectRepository:
    def save(self, project_root: Path, project: SimulationProject) -> None:
        raise EngineError(
            ErrorCode.PROJECT_IO_ERROR,
            "project save failed",
            {"path": str(Path(project_root) / "project.json"), "diagnostic": "disk full"},
        )

    def load(self, project_root: Path) -> SimulationProject:
        raise AssertionError("load is not used")


def _project_with_van_der_pol(root: Path) -> SimulationProject:
    model_path = root / "models" / VAN_DER_POL.name
    model_path.parent.mkdir(parents=True)
    shutil.copyfile(VAN_DER_POL, model_path)
    relative_path = f"models/{VAN_DER_POL.name}"
    asset = ModelAsset("vdp", "Van der Pol", relative_path, _sha256(model_path))
    case = SimulationCase(
        "case-1", "Nominal",
        SimulationGraph(nodes=(ModelNode("plant", relative_path, ModelNodeConfig(selected_outputs=("x0",), execution_interface=InterfaceType.CO_SIMULATION)),)),
        GraphSimulationConfig(stop_time=0.02, communication_step=0.01),
    )
    return SimulationProject("project", "Project", (asset,), (case,))


def _project_without_runtime(root: Path) -> SimulationProject:
    root.mkdir(parents=True)
    asset = ModelAsset("plant", "Plant", "models/plant.fmu", "a" * 64)
    case = SimulationCase("case-1", "Case", SimulationGraph(nodes=(ModelNode("plant", asset.relative_path),)), GraphSimulationConfig(stop_time=0.02, communication_step=0.01))
    return SimulationProject("project", "Project", (asset,), (case,))


def _result() -> GraphSimulationResult:
    return GraphSimulationResult(0.0, 0.02, 0.01, 2, 0.02, SimulationState.COMPLETED, (0.0, 0.01, 0.02), {"plant": {"x0": (2.0, 2.0, 2.0)}})


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
