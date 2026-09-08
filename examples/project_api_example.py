"""Run a complete SimulationProject workflow through Farcel's public API."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

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


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_FMU = REPOSITORY_ROOT / "examples" / "fmus" / "VanDerPol.fmu"
RELATIVE_MODEL_PATH = "models/VanDerPol.fmu"


def main() -> None:
    with TemporaryDirectory() as directory:
        project_root = Path(directory) / "VanDerPolProject"
        model_path = project_root / RELATIVE_MODEL_PATH
        model_path.parent.mkdir(parents=True)
        shutil.copyfile(SOURCE_FMU, model_path)

        asset = ModelAsset(
            asset_id="van-der-pol",
            display_name="Van der Pol",
            relative_path=RELATIVE_MODEL_PATH,
            sha256=_sha256(model_path),
        )
        case = SimulationCase(
            case_id="nominal",
            name="Nominal",
            graph=SimulationGraph(
                nodes=(
                    ModelNode(
                        "plant",
                        RELATIVE_MODEL_PATH,
                        ModelNodeConfig(
                            selected_outputs=("x0",),
                            execution_interface=InterfaceType.CO_SIMULATION,
                        ),
                    ),
                )
            ),
            config=GraphSimulationConfig(
                start_time=0.0,
                stop_time=0.02,
                communication_step=0.01,
            ),
        )
        project = SimulationProject(
            project_id="van-der-pol-project",
            name="Van der Pol Project",
            model_assets=(asset,),
            simulation_cases=(case,),
        )
        backend = create_backend()

        backend.save_project(project_root, project)
        print(f"project.json created: {(project_root / 'project.json').is_file()}")
        project = backend.open_project(project_root)
        report = backend.validate_project(project_root, project)
        assert report.is_valid
        print(f"project validation successful: {report.is_valid}")

        project, record, result = backend.run_project_case(
            project_root, project, "nominal"
        )
        assert result.completed_steps == 2
        assert result.sample_count == 3
        assert len(project.run_history) == 1
        print(f"completion state: {result.completion_state.name}")
        print(f"completed steps: {result.completed_steps}")
        print(f"samples: {result.sample_count}")
        print(f"final time: {result.final_time}")
        print(f"run id: {record.run_id}")
        print(f"result path: {record.result_path}")
        print(f"history count: {len(project.run_history)}")
        print(f"first x0: {result.node_outputs['plant']['x0'][0]}")
        print(f"last x0: {result.node_outputs['plant']['x0'][-1]}")

        reopened = backend.open_project(project_root)
        assert reopened == project
        assert reopened.run_history[0].run_id == record.run_id
        artifact = backend.load_project_run(project_root, reopened, record.run_id)
        assert artifact.result == result
        assert artifact.case_snapshot.graph.nodes[0].model_path == RELATIVE_MODEL_PATH
        print(f"historical result restored: {artifact.result == result}")
        print(f"historical model path: {artifact.case_snapshot.graph.nodes[0].model_path}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    main()
