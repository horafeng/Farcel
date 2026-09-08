import hashlib
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from farcel import create_backend
from farcel.contracts import (
    EngineError,
    ErrorCode,
    GraphSimulationConfig,
    InterfaceType,
    ModelAsset,
    ModelNode,
    ModelNodeConfig,
    SimulationCase,
    SimulationGraph,
    SimulationProject,
    SimulationState,
)


ROOT = Path(__file__).resolve().parents[2]
VAN_DER_POL = ROOT / "examples" / "fmus" / "VanDerPol.fmu"


@unittest.skipUnless(VAN_DER_POL.is_file(), "VanDerPol FMU is unavailable")
class ProjectExecutionPublicApiIntegrationTests(unittest.TestCase):
    def test_two_public_runs_commit_history_and_make_artifacts_immediately_readable(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "Project"
            original_project = _project_with_van_der_pol(root)
            backend = create_backend()
            backend.save_project(root, original_project)

            project_1, record_1, result_1 = backend.run_project_case(root, original_project, "case-1")
            reopened_1 = backend.open_project(root)
            artifact_1 = backend.load_project_run(root, reopened_1, record_1.run_id)
            project_2, record_2, result_2 = backend.run_project_case(root, project_1, "case-1")
            reopened_2 = backend.open_project(root)
            artifact_2 = backend.load_project_run(root, reopened_2, record_2.run_id)

        self.assertEqual(original_project.run_history, ())
        self.assertEqual(project_1.run_history, (record_1,))
        self.assertEqual(project_2.run_history, (record_1, record_2))
        self.assertEqual(reopened_1, project_1)
        self.assertEqual(reopened_2, project_2)
        self.assertNotEqual(record_1.run_id, record_2.run_id)
        for record, result, artifact in (
            (record_1, result_1, artifact_1),
            (record_2, result_2, artifact_2),
        ):
            self.assertIs(result.completion_state, SimulationState.COMPLETED)
            self.assertEqual((result.completed_steps, result.final_time, result.sample_count), (2, 0.02, 3))
            self.assertIn("x0", result.node_outputs["plant"])
            self.assertEqual(record.case_id, "case-1")
            self.assertEqual(record.completion_state, result.completion_state)
            self.assertEqual(record.final_time, result.final_time)
            self.assertEqual(record.completed_steps, result.completed_steps)
            self.assertTrue(record.run_id)
            self.assertEqual(record.result_path, f"results/{record.run_id}.json")
            self.assertEqual(artifact.run_id, record.run_id)
            self.assertEqual(artifact.project_id, reopened_2.project_id)
            self.assertEqual(artifact.case_snapshot.case_id, record.case_id)
            self.assertEqual(artifact.case_snapshot.graph.nodes[0].model_path, "models/VanDerPol.fmu")
            self.assertEqual(artifact.result, result)

    def test_invalid_project_and_unknown_case_do_not_create_history_or_artifacts(self) -> None:
        with TemporaryDirectory() as directory:
            invalid_root = Path(directory) / "Invalid"
            invalid_project = _project_with_van_der_pol(invalid_root, checksum="0" * 64)
            backend = create_backend()
            backend.save_project(invalid_root, invalid_project)

            with self.assertRaises(EngineError) as invalid:
                backend.run_project_case(invalid_root, invalid_project, "case-1")

            valid_root = Path(directory) / "Valid"
            valid_project = _project_with_van_der_pol(valid_root)
            backend.save_project(valid_root, valid_project)
            with self.assertRaises(EngineError) as unknown:
                backend.run_project_case(valid_root, valid_project, "missing-case")

            self.assertFalse((invalid_root / "results").exists())
            self.assertFalse((valid_root / "results").exists())
            self.assertEqual(backend.open_project(invalid_root).run_history, ())
            self.assertEqual(backend.open_project(valid_root).run_history, ())

        self.assertEqual(invalid.exception.code, ErrorCode.CONFIG_ERROR)
        self.assertIn(
            "ASSET_CHECKSUM_MISMATCH",
            tuple(issue["code"] for issue in invalid.exception.details["issues"]),
        )
        self.assertEqual(unknown.exception.code, ErrorCode.CONFIG_ERROR)
        self.assertEqual(unknown.exception.details["issues"][0]["code"], "UNKNOWN_CASE_ID")


def _project_with_van_der_pol(root: Path, checksum: str | None = None) -> SimulationProject:
    model_path = root / "models" / VAN_DER_POL.name
    model_path.parent.mkdir(parents=True)
    shutil.copyfile(VAN_DER_POL, model_path)
    relative_path = "models/VanDerPol.fmu"
    asset = ModelAsset(
        "vdp",
        "Van der Pol",
        relative_path,
        _sha256(model_path) if checksum is None else checksum,
    )
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
