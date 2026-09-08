import hashlib
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from farcel import create_backend
from farcel.application.project_run_persistence import ProjectRunPersistenceService
from farcel.contracts import (
    EngineError,
    ErrorCode,
    GraphSimulationConfig,
    GraphSimulationResult,
    InterfaceType,
    ModelAsset,
    ModelNode,
    ModelNodeConfig,
    ProjectRunRecord,
    SimulationCase,
    SimulationGraph,
    SimulationProject,
    SimulationState,
)
from farcel.infrastructure.project import (
    LocalJsonProjectRepository,
    LocalJsonProjectRunArtifactRepository,
)


ROOT = Path(__file__).resolve().parents[2]
VAN_DER_POL = ROOT / "examples" / "fmus" / "VanDerPol.fmu"


@unittest.skipUnless(VAN_DER_POL.is_file(), "VanDerPol FMU is unavailable")
class ProjectRunPublicApiIntegrationTests(unittest.TestCase):
    def test_public_historical_load_is_read_only_relocatable_and_independent_of_current_assets(self) -> None:
        with TemporaryDirectory() as directory:
            location_a = Path(directory) / "LocationA" / "Project"
            location_b = Path(directory) / "LocationB" / "Project"
            project = _project_with_van_der_pol(location_a)
            repository = LocalJsonProjectRepository()
            artifact_repository = LocalJsonProjectRunArtifactRepository()
            persisted, record = ProjectRunPersistenceService(
                repository, artifact_repository, lambda: "run-history"
            ).persist_run(location_a, project, "case-1", _result())
            project_json_before = (location_a / "project.json").read_bytes()
            artifact_before = (location_a / record.result_path).read_bytes()
            backend = create_backend()

            reopened_a = backend.open_project(location_a)
            artifact_a = backend.load_project_run(location_a, reopened_a, "run-history")

            self.assertEqual((location_a / "project.json").read_bytes(), project_json_before)
            self.assertEqual((location_a / record.result_path).read_bytes(), artifact_before)
            self.assertEqual(reopened_a, persisted)
            self.assertEqual(artifact_a.run_id, "run-history")
            self.assertEqual(artifact_a.project_id, reopened_a.project_id)
            self.assertEqual(artifact_a.case_snapshot.case_id, record.case_id)
            self.assertEqual(
                artifact_a.case_snapshot.graph.nodes[0].model_path,
                "models/VanDerPol.fmu",
            )
            self.assertEqual(artifact_a.result.completion_state, record.completion_state)
            self.assertEqual(artifact_a.result.final_time, record.final_time)
            self.assertEqual(artifact_a.result.completed_steps, record.completed_steps)
            self.assertEqual(artifact_a.result.node_outputs["plant"]["x0"], (2.0, 2.0, 2.0))

            shutil.copytree(location_a, location_b)
            (location_b / "models" / VAN_DER_POL.name).unlink()
            project_json_b_before = (location_b / "project.json").read_bytes()
            artifact_b_before = (location_b / record.result_path).read_bytes()
            reopened_b = backend.open_project(location_b)
            artifact_b = backend.load_project_run(location_b, reopened_b, "run-history")

            self.assertEqual(artifact_b, artifact_a)
            self.assertEqual((location_b / "project.json").read_bytes(), project_json_b_before)
            self.assertEqual((location_b / record.result_path).read_bytes(), artifact_b_before)
            self.assertEqual(reopened_b.run_history, (record,))
            with self.assertRaises(EngineError) as invalid_current_project:
                backend.validate_project(location_b, reopened_b)
            self.assertEqual(invalid_current_project.exception.code, ErrorCode.CONFIG_ERROR)
            self.assertIn(
                "ASSET_MISSING",
                tuple(
                    issue["code"]
                    for issue in invalid_current_project.exception.details["issues"]
                ),
            )


class ProjectRunPublicApiErrorIntegrationTests(unittest.TestCase):
    def test_missing_history_artifact_propagates_project_io_error(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "Project"
            project = SimulationProject(
                "project",
                "Project",
                run_history=(
                    ProjectRunRecord(
                        "run-missing",
                        "former-case",
                        SimulationState.COMPLETED,
                        0.02,
                        2,
                        "results/run-missing.json",
                    ),
                ),
            )
            backend = create_backend()
            backend.save_project(root, project)
            reopened = backend.open_project(root)

            with self.assertRaises(EngineError) as raised:
                backend.load_project_run(root, reopened, "run-missing")

        self.assertEqual(raised.exception.code, ErrorCode.PROJECT_IO_ERROR)


def _project_with_van_der_pol(root: Path) -> SimulationProject:
    model_path = root / "models" / VAN_DER_POL.name
    model_path.parent.mkdir(parents=True)
    shutil.copyfile(VAN_DER_POL, model_path)
    relative_path = "models/VanDerPol.fmu"
    asset = ModelAsset("vdp", "Van der Pol", relative_path, _sha256(model_path))
    case = SimulationCase(
        "case-1",
        "Nominal",
        SimulationGraph(
            nodes=(
                ModelNode(
                    "plant",
                    relative_path,
                    ModelNodeConfig(
                        selected_outputs=("x0",),
                        execution_interface=InterfaceType.CO_SIMULATION,
                    ),
                ),
            )
        ),
        GraphSimulationConfig(stop_time=0.02, communication_step=0.01),
    )
    return SimulationProject("project", "Project", (asset,), (case,))


def _result() -> GraphSimulationResult:
    return GraphSimulationResult(
        0.0,
        0.02,
        0.01,
        2,
        0.02,
        SimulationState.COMPLETED,
        (0.0, 0.01, 0.02),
        {"plant": {"x0": (2.0, 2.0, 2.0)}},
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
