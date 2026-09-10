"""Run Farcel's complete Phase 6 scenario, history, export, and comparison workflow."""

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
    SignalStatisticsStatus,
    SimulationCase,
    SimulationGraph,
    SimulationProject,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_FMU = REPOSITORY_ROOT / "examples" / "fmus" / "VanDerPol.fmu"
RELATIVE_MODEL_PATH = "models/VanDerPol.fmu"


def main() -> None:
    with TemporaryDirectory() as directory:
        project_root = Path(directory) / "Phase6Project"
        model_path = project_root / RELATIVE_MODEL_PATH
        model_path.parent.mkdir(parents=True)
        shutil.copyfile(SOURCE_FMU, model_path)

        project = _project(model_path)
        backend = create_backend()
        backend.save_project(project_root, project)
        project = backend.open_project(project_root)
        assert backend.validate_project(project_root, project).is_valid

        batch = backend.run_project_cases(
            project_root,
            project,
            ("high_mu", "baseline"),
        )
        assert batch.completed_count == 2
        assert not batch.stopped
        assert tuple(item.case_id for item in batch.items) == ("high_mu", "baseline")
        assert batch.items[0].record.run_id != batch.items[1].record.run_id
        print(f"batch case order: {tuple(item.case_id for item in batch.items)}")
        print(f"batch run ids: {tuple(item.record.run_id for item in batch.items)}")

        artifact_high = backend.load_project_run(
            project_root, batch.updated_project, batch.items[0].record.run_id
        )
        artifact_base = backend.load_project_run(
            project_root, batch.updated_project, batch.items[1].record.run_id
        )

        for label, artifact in (("high_mu", artifact_high), ("baseline", artifact_base)):
            destination = project_root / "exports" / f"{label}.csv"
            report = backend.export_graph_result(artifact.result, destination)
            assert destination.is_file()
            assert report.row_count == artifact.result.sample_count
            print(f"{label} CSV rows: {report.row_count}")

        comparison = backend.compare_project_runs((artifact_high, artifact_base))
        signal = next(
            item
            for item in comparison.signals
            if item.node_id == "plant" and item.variable_name == "x0"
        )
        assert tuple(series.run_id for series in signal.series) == (
            artifact_high.run_id,
            artifact_base.run_id,
        )
        for series in signal.series:
            statistics = series.statistics
            assert statistics.status is SignalStatisticsStatus.AVAILABLE
            print(
                f"run {series.run_id}: samples={len(series.timestamps)}, "
                f"min={statistics.minimum}, max={statistics.maximum}, "
                f"mean={statistics.mean}, final={statistics.final}"
            )


def _project(model_path: Path) -> SimulationProject:
    asset = ModelAsset(
        asset_id="van-der-pol",
        display_name="Van der Pol",
        relative_path=RELATIVE_MODEL_PATH,
        sha256=_sha256(model_path),
    )
    cases = tuple(
        SimulationCase(
            case_id=case_id,
            name=name,
            graph=SimulationGraph(
                nodes=(
                    ModelNode(
                        "plant",
                        RELATIVE_MODEL_PATH,
                        ModelNodeConfig(
                            parameters={"mu": mu},
                            selected_outputs=("x0",),
                            execution_interface=InterfaceType.CO_SIMULATION,
                        ),
                    ),
                )
            ),
            config=GraphSimulationConfig(
                start_time=0.0,
                stop_time=0.05,
                communication_step=0.01,
            ),
        )
        for case_id, name, mu in (
            ("baseline", "Baseline", 1.0),
            ("high_mu", "High mu", 2.0),
        )
    )
    return SimulationProject(
        project_id="phase6-van-der-pol",
        name="Phase 6 Van der Pol",
        model_assets=(asset,),
        simulation_cases=cases,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    main()
