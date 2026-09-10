from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from pathlib import Path
import unittest

from farcel import create_backend
from farcel.application.engine import FarcelEngine
from farcel.application.project_comparison import _statistics_for
from farcel.contracts import (
    EngineError,
    ErrorCode,
    GraphSimulationConfig,
    GraphSimulationResult,
    ModelNode,
    ProjectAssetSnapshot,
    ProjectRunArtifact,
    ProjectRunComparison,
    ProjectRunSignalComparison,
    ProjectRunSignalSeries,
    SignalStatistics,
    SignalStatisticsStatus,
    SimulationCase,
    SimulationGraph,
    SimulationState,
)


class _FailingImporter:
    def load(self, path: Path):
        raise AssertionError("comparison must not import an FMU")


class ProjectRunComparisonTests(unittest.TestCase):
    def test_empty_artifacts_are_rejected(self) -> None:
        with self.assertRaises(EngineError) as raised:
            FarcelEngine(_FailingImporter()).compare_project_runs(())

        self.assertIs(raised.exception.code, ErrorCode.CONFIG_ERROR)
        self.assertEqual(raised.exception.details["issues"][0]["field"], "artifacts")
        self.assertEqual(
            raised.exception.details["issues"][0]["code"],
            "EMPTY_COMPARISON_ARTIFACTS",
        )

    def test_duplicate_run_ids_are_rejected_before_processing(self) -> None:
        artifacts = (_artifact("run-a"), _artifact("run-b"), _artifact("run-a"))

        with self.assertRaises(EngineError) as raised:
            FarcelEngine(_FailingImporter()).compare_project_runs(artifacts)

        self.assertIs(raised.exception.code, ErrorCode.CONFIG_ERROR)
        self.assertEqual(
            raised.exception.details["issues"][0]["field"], "artifacts[2].run_id"
        )
        self.assertEqual(
            raised.exception.details["issues"][0]["code"],
            "DUPLICATE_COMPARISON_RUN_ID",
        )

    def test_one_run_is_allowed_and_preserves_source_identity(self) -> None:
        artifact = _artifact("one", outputs={"plant": {"x": (1, 2, 3)}})

        comparison = FarcelEngine(_FailingImporter()).compare_project_runs((artifact,))

        self.assertEqual(comparison.sources, (artifact,))
        self.assertEqual(comparison.signals[0].series[0].statistics.mean, 2.0)

    def test_source_and_each_series_preserve_caller_order(self) -> None:
        artifacts = tuple(_artifact(run_id) for run_id in ("run-C", "run-A", "run-B"))

        comparison = FarcelEngine(_FailingImporter()).compare_project_runs(artifacts)

        self.assertEqual(tuple(source.run_id for source in comparison.sources), ("run-C", "run-A", "run-B"))
        self.assertEqual(
            tuple(series.run_id for series in comparison.signals[0].series),
            ("run-C", "run-A", "run-B"),
        )

    def test_signal_union_uses_structural_unicode_order(self) -> None:
        first = _artifact("first", outputs={"B": {"z": (1, 2, 3)}, "A": {"y": (1, 2, 3)}})
        second = _artifact("second", outputs={"A": {"x": (1, 2, 3)}, "B": {"a": (1, 2, 3)}})

        comparison = FarcelEngine(_FailingImporter()).compare_project_runs((first, second))

        self.assertEqual(
            tuple((signal.node_id, signal.variable_name) for signal in comparison.signals),
            (("A", "x"), ("A", "y"), ("B", "a"), ("B", "z")),
        )

    def test_numeric_scalars_receive_basic_statistics(self) -> None:
        artifact = _artifact(
            "run",
            timestamps=(0.0, 0.1, 0.2, 0.3),
            outputs={"plant": {"x": (2.0, -1.0, 5.0, 4.0)}},
        )

        statistics = FarcelEngine(_FailingImporter()).compare_project_runs((artifact,)).signals[0].series[0].statistics

        self.assertIs(statistics.status, SignalStatisticsStatus.AVAILABLE)
        self.assertTrue(statistics.available)
        self.assertEqual((statistics.minimum, statistics.maximum, statistics.mean, statistics.final), (-1.0, 5.0, 2.5, 4.0))

    def test_integer_scalars_are_numeric(self) -> None:
        artifact = _artifact("run", outputs={"plant": {"x": (1, 2, 3)}})

        statistics = FarcelEngine(_FailingImporter()).compare_project_runs((artifact,)).signals[0].series[0].statistics

        self.assertIs(statistics.status, SignalStatisticsStatus.AVAILABLE)
        self.assertEqual(statistics.mean, 2.0)

    def test_bool_and_strings_are_never_numeric(self) -> None:
        artifact = _artifact(
            "run",
            outputs={"plant": {"bool": (True, False, True), "text": ("a", "b", "c")}},
        )

        comparison = FarcelEngine(_FailingImporter()).compare_project_runs((artifact,))
        statuses = {signal.variable_name: signal.series[0].statistics for signal in comparison.signals}

        for statistics in statuses.values():
            self.assertIs(statistics.status, SignalStatisticsStatus.NON_NUMERIC_SIGNAL)
            self.assertFalse(statistics.available)
            self.assertEqual((statistics.minimum, statistics.maximum, statistics.mean, statistics.final), (None, None, None, None))
        self.assertEqual(next(signal for signal in comparison.signals if signal.variable_name == "text").series[0].samples, ("a", "b", "c"))

    def test_one_and_two_dimensional_arrays_are_retained_without_expansion(self) -> None:
        artifact = _artifact(
            "run",
            outputs={
                "plant": {
                    "one": ((1.0, 2.0), (3.0, 4.0), (5.0, 6.0)),
                    "two": (((1.0, 2.0), (3.0, 4.0)), ((5.0, 6.0), (7.0, 8.0)), ((9.0, 10.0), (11.0, 12.0))),
                }
            },
        )

        comparison = FarcelEngine(_FailingImporter()).compare_project_runs((artifact,))

        self.assertEqual(tuple(signal.variable_name for signal in comparison.signals), ("one", "two"))
        for signal in comparison.signals:
            self.assertIs(signal.series[0].statistics.status, SignalStatisticsStatus.ARRAY_SIGNAL)
            self.assertEqual((signal.series[0].statistics.minimum, signal.series[0].statistics.maximum, signal.series[0].statistics.mean, signal.series[0].statistics.final), (None, None, None, None))
        self.assertEqual(comparison.signals[1].series[0].samples[0], ((1.0, 2.0), (3.0, 4.0)))

    def test_missing_signal_has_an_explicit_empty_series(self) -> None:
        first = _artifact("run-A", outputs={"plant": {"x": (1, 2, 3), "y": (4, 5, 6)}})
        second = _artifact("run-B", outputs={"plant": {"x": (7, 8, 9)}})

        comparison = FarcelEngine(_FailingImporter()).compare_project_runs((first, second))
        y = next(signal for signal in comparison.signals if signal.variable_name == "y")

        self.assertEqual(y.series[0].timestamps, (0.0, 0.1, 0.2))
        self.assertEqual(y.series[0].samples, (4, 5, 6))
        self.assertEqual(y.series[1].timestamps, ())
        self.assertEqual(y.series[1].samples, ())
        self.assertIs(y.series[1].statistics.status, SignalStatisticsStatus.MISSING_SIGNAL)

    def test_native_time_axes_are_not_aligned_or_resampled(self) -> None:
        first = _artifact("run-A", timestamps=(0.0, 0.1, 0.2), outputs={"plant": {"x": (1, 2, 3)}})
        second = _artifact("run-B", timestamps=(0.0, 0.05, 0.1, 0.15, 0.2), outputs={"plant": {"x": (4, 5, 6, 7, 8)}})

        series = FarcelEngine(_FailingImporter()).compare_project_runs((first, second)).signals[0].series

        self.assertEqual(series[0].timestamps, first.result.timestamps)
        self.assertEqual(series[1].timestamps, second.result.timestamps)

    def test_stopped_artifact_uses_only_recorded_samples_and_keeps_provenance(self) -> None:
        stopped = _artifact(
            "stopped",
            state=SimulationState.STOPPED,
            timestamps=(0.0, 0.1, 0.2),
            stop_time=1.0,
            outputs={"plant": {"x": (2.0, 4.0, 8.0)}},
        )

        comparison = FarcelEngine(_FailingImporter()).compare_project_runs((stopped,))
        series = comparison.signals[0].series[0]

        self.assertIs(comparison.sources[0], stopped)
        self.assertIs(comparison.sources[0].result.completion_state, SimulationState.STOPPED)
        self.assertEqual(comparison.sources[0].result.final_time, 0.2)
        self.assertEqual(series.timestamps, (0.0, 0.1, 0.2))
        self.assertEqual(series.statistics.mean, 14.0 / 3.0)

    def test_different_projects_and_provenance_are_allowed(self) -> None:
        first = _artifact("run-1", project_id="project-A", case_id="case-A", asset_id="asset-A")
        second = _artifact("run-9", project_id="project-B", case_id="case-B", asset_id="asset-B")

        comparison = FarcelEngine(_FailingImporter()).compare_project_runs((first, second))

        self.assertEqual(comparison.sources, (first, second))
        self.assertEqual(tuple(source.project_id for source in comparison.sources), ("project-A", "project-B"))
        self.assertEqual(tuple(source.case_snapshot.case_id for source in comparison.sources), ("case-A", "case-B"))
        self.assertEqual(tuple(source.asset_snapshots[0].asset_id for source in comparison.sources), ("asset-A", "asset-B"))

    def test_engine_and_default_backend_are_pure_public_apis(self) -> None:
        artifact = _artifact("run")

        engine_comparison = FarcelEngine(_FailingImporter()).compare_project_runs((artifact,))
        backend_comparison = create_backend().compare_project_runs((artifact,))

        self.assertEqual(engine_comparison, backend_comparison)

    def test_empty_signal_helper_is_explicitly_unavailable(self) -> None:
        statistics = _statistics_for(())

        self.assertIs(statistics.status, SignalStatisticsStatus.EMPTY_SIGNAL)
        self.assertEqual((statistics.minimum, statistics.maximum, statistics.mean, statistics.final), (None, None, None, None))

    def test_comparison_contracts_are_frozen_slots_public_dtos(self) -> None:
        statistics = SignalStatistics(SignalStatisticsStatus.AVAILABLE, 1.0, 2.0, 1.5, 2.0)
        series = ProjectRunSignalSeries("run", (0.0,), (2.0,), statistics)
        comparison = ProjectRunSignalComparison("node", "x", (series,))
        result = ProjectRunComparison((_artifact("run"),), (comparison,))

        self.assertEqual([field.name for field in fields(SignalStatistics)], ["status", "minimum", "maximum", "mean", "final"])
        for contract_type in (SignalStatistics, ProjectRunSignalSeries, ProjectRunSignalComparison, ProjectRunComparison):
            self.assertTrue(hasattr(contract_type, "__slots__"))
        with self.assertRaises(FrozenInstanceError):
            result.sources = ()
        with self.assertRaises(ValueError):
            SignalStatistics(SignalStatisticsStatus.MISSING_SIGNAL, minimum=0.0)


def _artifact(
    run_id: str,
    *,
    project_id: str = "project",
    case_id: str = "case",
    asset_id: str = "asset",
    timestamps: tuple[float, ...] = (0.0, 0.1, 0.2),
    outputs: dict[str, dict[str, tuple[object, ...]]] | None = None,
    state: SimulationState = SimulationState.COMPLETED,
    stop_time: float | None = None,
) -> ProjectRunArtifact:
    effective_stop_time = timestamps[-1] if stop_time is None else stop_time
    result = GraphSimulationResult(
        start_time=timestamps[0],
        stop_time=effective_stop_time,
        step_size=0.1,
        completed_steps=len(timestamps) - 1,
        final_time=timestamps[-1],
        completion_state=state,
        timestamps=timestamps,
        node_outputs={"plant": {"x": (1.0, 2.0, 3.0)}} if outputs is None else outputs,
    )
    return ProjectRunArtifact(
        run_id=run_id,
        project_id=project_id,
        case_snapshot=SimulationCase(
            case_id,
            f"Case {case_id}",
            SimulationGraph(nodes=(ModelNode("plant", "models/plant.fmu"),)),
            GraphSimulationConfig(stop_time=effective_stop_time, communication_step=0.1),
        ),
        asset_snapshots=(ProjectAssetSnapshot(asset_id, "models/plant.fmu", "a" * 64),),
        result=result,
    )
