import hashlib
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from farcel import create_backend
from farcel.application.project_service import ProjectService
from farcel.application.project_validation import ProjectValidator
from farcel.contracts import (
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
from farcel.infrastructure.fmpy import FmpyImporter


ROOT = Path(__file__).resolve().parents[2]
VAN_DER_POL = ROOT / "examples" / "fmus" / "VanDerPol.fmu"


@unittest.skipUnless(VAN_DER_POL.is_file(), "VanDerPol FMU is unavailable")
class ProjectCaseRunIntegrationTests(unittest.TestCase):
    def test_real_project_case_reuses_backend_graph_execution(self) -> None:
        with TemporaryDirectory() as directory:
            project_root = Path(directory) / "Project"
            model_path = project_root / "models" / VAN_DER_POL.name
            model_path.parent.mkdir(parents=True)
            shutil.copyfile(VAN_DER_POL, model_path)
            relative_path = f"models/{VAN_DER_POL.name}"
            asset = ModelAsset("vdp", "Van der Pol", relative_path, _sha256(model_path))
            graph = SimulationGraph(
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
            )
            case = SimulationCase(
                "case-1",
                "Nominal",
                graph,
                GraphSimulationConfig(stop_time=0.02, communication_step=0.01),
            )
            project = SimulationProject("project", "Project", (asset,), (case,))
            backend = create_backend()
            service = ProjectService(ProjectValidator(FmpyImporter()), backend.run_graph)

            result = service.run_case(project_root, project, "case-1")

        self.assertIs(result.completion_state, SimulationState.COMPLETED)
        self.assertEqual(result.completed_steps, 2)
        self.assertEqual(result.final_time, 0.02)
        self.assertEqual(result.sample_count, 3)
        self.assertIn("x0", result.node_outputs["plant"])
        self.assertEqual(case.graph.nodes[0].model_path, relative_path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
