import hashlib
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from farcel import create_backend
from farcel.contracts import (
    GraphSimulationConfig,
    InterfaceType,
    ModelAsset,
    ModelNode,
    ModelNodeConfig,
    SimulationCase,
    SimulationGraph,
    SimulationProject,
)


ROOT = Path(__file__).resolve().parents[2]
VAN_DER_POL = ROOT / "examples" / "fmus" / "VanDerPol.fmu"


@unittest.skipUnless(VAN_DER_POL.is_file(), "VanDerPol FMU is unavailable")
class ProjectBatchPublicApiIntegrationTests(unittest.TestCase):
    def test_default_backend_persists_each_requested_case_in_request_order(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "Project"
            project = _project_with_two_cases(root)
            backend = create_backend()
            backend.save_project(root, project)

            batch = backend.run_project_cases(root, project, ("case-2", "case-1"))
            reopened = backend.open_project(root)

            self.assertEqual(tuple(item.case_id for item in batch.items), ("case-2", "case-1"))
            self.assertEqual(batch.completed_count, 2)
            self.assertFalse(batch.stopped)
            self.assertEqual(reopened, batch.updated_project)
            self.assertEqual(
                tuple(record.case_id for record in batch.updated_project.run_history),
                ("case-2", "case-1"),
            )
            self.assertNotEqual(batch.items[0].record.run_id, batch.items[1].record.run_id)
            for item in batch.items:
                self.assertTrue((root / item.record.result_path).is_file())


def _project_with_two_cases(root: Path) -> SimulationProject:
    model_path = root / "models" / VAN_DER_POL.name
    model_path.parent.mkdir(parents=True)
    shutil.copyfile(VAN_DER_POL, model_path)
    relative_path = "models/VanDerPol.fmu"
    asset = ModelAsset("vdp", "Van der Pol", relative_path, _sha256(model_path))
    cases = tuple(
        SimulationCase(
            case_id,
            case_id,
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
        for case_id in ("case-1", "case-2")
    )
    return SimulationProject("project", "Project", (asset,), cases)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
