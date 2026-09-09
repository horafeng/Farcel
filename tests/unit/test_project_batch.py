from dataclasses import replace
from pathlib import Path
import unittest

from farcel.application.project_batch import ProjectBatchService
from farcel.contracts import (
    EngineError,
    ErrorCode,
    GraphSimulationConfig,
    GraphSimulationResult,
    ModelNode,
    ProjectRunRecord,
    RunControl,
    RunProgress,
    SimulationCase,
    SimulationGraph,
    SimulationProject,
    SimulationState,
)


class _CaseRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, SimulationProject]] = []
        self.errors: dict[str, EngineError] = {}
        self.states: dict[str, SimulationState] = {}
        self.request_stop_after_case: str | None = None
        self.emit_progress = False

    def __call__(
        self,
        project_root: str | Path,
        project: SimulationProject,
        case_id: str,
        *,
        control: RunControl | None = None,
        on_progress=None,
    ):
        self.calls.append((case_id, project))
        if case_id in self.errors:
            raise self.errors[case_id]
        if self.emit_progress and on_progress is not None:
            on_progress(_progress())
        state = self.states.get(case_id, SimulationState.COMPLETED)
        result = _result(state)
        record = _record(f"run-{case_id}", case_id, state)
        updated = replace(project, run_history=(*project.run_history, record))
        if case_id == self.request_stop_after_case and control is not None:
            control.request_stop()
        return updated, record, result


class ProjectBatchServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = _CaseRunner()
        self.service = ProjectBatchService(self.runner)
        self.project = _project("a", "b", "c")

    def test_empty_duplicate_and_unknown_requests_fail_before_any_case_runs(self) -> None:
        cases = (
            ((), "EMPTY_CASE_IDS", "case_ids"),
            (("a", "b", "a"), "DUPLICATE_REQUESTED_CASE_ID", "case_ids[2]"),
            (("a", "missing"), "UNKNOWN_CASE_ID", "case_ids[1]"),
        )
        for case_ids, code, field in cases:
            with self.subTest(case_ids=case_ids):
                with self.assertRaises(EngineError) as raised:
                    self.service.run("root", self.project, case_ids)

                self.assertIs(raised.exception.code, ErrorCode.CONFIG_ERROR)
                self.assertIn(
                    (field, code),
                    tuple(
                        (issue["field"], issue["code"])
                        for issue in raised.exception.details["issues"]
                    ),
                )
                self.assertEqual(self.runner.calls, [])

    def test_caller_order_and_updated_project_are_chained(self) -> None:
        existing = _record("existing", "a", SimulationState.COMPLETED)
        project = replace(self.project, run_history=(existing,))

        batch = self.service.run("root", project, ("c", "a", "b"))

        self.assertEqual(tuple(case_id for case_id, _ in self.runner.calls), ("c", "a", "b"))
        self.assertEqual(tuple(item.case_id for item in batch.items), ("c", "a", "b"))
        self.assertEqual(
            tuple(record.run_id for record in self.runner.calls[1][1].run_history),
            ("existing", "run-c"),
        )
        self.assertEqual(
            tuple(record.run_id for record in self.runner.calls[2][1].run_history),
            ("existing", "run-c", "run-a"),
        )
        self.assertEqual(
            tuple(record.run_id for record in batch.updated_project.run_history),
            ("existing", "run-c", "run-a", "run-b"),
        )
        self.assertEqual(batch.completed_count, 3)
        self.assertFalse(batch.stopped)

    def test_items_have_independent_records_and_results(self) -> None:
        batch = self.service.run("root", self.project, ("a", "b"))

        self.assertEqual(tuple(item.record.case_id for item in batch.items), ("a", "b"))
        self.assertEqual(tuple(item.record.run_id for item in batch.items), ("run-a", "run-b"))
        self.assertEqual(
            tuple(item.record.result_path for item in batch.items),
            ("results/run-a.json", "results/run-b.json"),
        )

    def test_progress_wraps_the_exact_existing_run_progress(self) -> None:
        self.runner.emit_progress = True
        received = []

        self.service.run("root", self.project, ("b", "a"), on_progress=received.append)

        self.assertEqual(
            tuple((item.case_id, item.case_number, item.case_count) for item in received),
            (("b", 1, 2), ("a", 2, 2)),
        )
        self.assertEqual(tuple(item.run_progress for item in received), (_progress(), _progress()))

    def test_prestart_cancelled_runs_no_case(self) -> None:
        control = RunControl()
        control.request_stop()

        with self.assertRaises(EngineError) as raised:
            self.service.run("root", self.project, ("a",), control=control)

        self.assertIs(raised.exception.code, ErrorCode.CANCELLED)
        self.assertEqual(self.runner.calls, [])

    def test_stopped_item_is_retained_and_stops_the_batch(self) -> None:
        self.runner.states["a"] = SimulationState.STOPPED

        batch = self.service.run("root", self.project, ("a", "b"))

        self.assertTrue(batch.stopped)
        self.assertEqual(tuple(item.case_id for item in batch.items), ("a",))
        self.assertIs(batch.items[0].result.completion_state, SimulationState.STOPPED)
        self.assertEqual(tuple(case_id for case_id, _ in self.runner.calls), ("a",))

    def test_stop_between_items_retains_completed_item(self) -> None:
        control = RunControl()
        self.runner.request_stop_after_case = "a"

        batch = self.service.run("root", self.project, ("a", "b"), control=control)

        self.assertTrue(batch.stopped)
        self.assertEqual(tuple(item.case_id for item in batch.items), ("a",))
        self.assertEqual(tuple(case_id for case_id, _ in self.runner.calls), ("a",))

    def test_failure_preserves_error_and_adds_completed_batch_context(self) -> None:
        self.runner.errors["c"] = EngineError(
            ErrorCode.STEP_ERROR, "step failed", {"time": 0.02}
        )

        with self.assertRaises(EngineError) as raised:
            self.service.run("root", self.project, ("a", "b", "c"))

        error = raised.exception
        self.assertIs(error.code, ErrorCode.STEP_ERROR)
        self.assertEqual(error.message, "step failed")
        self.assertEqual(error.details["time"], 0.02)
        self.assertEqual(error.details["batch_failed_case_id"], "c")
        self.assertEqual(error.details["batch_failed_case_number"], 3)
        self.assertEqual(error.details["batch_case_count"], 3)
        self.assertEqual(error.details["batch_completed_case_ids"], ("a", "b"))
        self.assertEqual(error.details["batch_completed_run_ids"], ("run-a", "run-b"))

    def test_persistence_diagnostics_survive_batch_context(self) -> None:
        self.runner.errors["a"] = EngineError(
            ErrorCode.PROJECT_IO_ERROR,
            "project save failed",
            {"orphan_result_path": "results/run-a.json", "history_committed": False},
        )

        with self.assertRaises(EngineError) as raised:
            self.service.run("root", self.project, ("a", "b"))

        error = raised.exception
        self.assertIs(error.code, ErrorCode.PROJECT_IO_ERROR)
        self.assertEqual(error.details["orphan_result_path"], "results/run-a.json")
        self.assertFalse(error.details["history_committed"])
        self.assertEqual(error.details["batch_completed_case_ids"], ())


def _project(*case_ids: str) -> SimulationProject:
    return SimulationProject(
        "project",
        "Project",
        simulation_cases=tuple(
            SimulationCase(
                case_id,
                case_id,
                SimulationGraph(nodes=(ModelNode("plant", "models/plant.fmu"),)),
                GraphSimulationConfig(stop_time=0.02, communication_step=0.01),
            )
            for case_id in case_ids
        ),
    )


def _record(run_id: str, case_id: str, state: SimulationState) -> ProjectRunRecord:
    return ProjectRunRecord(run_id, case_id, state, 0.02, 2, f"results/{run_id}.json")


def _result(state: SimulationState) -> GraphSimulationResult:
    final_time = 0.01 if state is SimulationState.STOPPED else 0.02
    timestamps = (0.0, 0.01) if state is SimulationState.STOPPED else (0.0, 0.01, 0.02)
    return GraphSimulationResult(
        0.0,
        0.02,
        0.01,
        len(timestamps) - 1,
        final_time,
        state,
        timestamps,
        {"plant": {"x0": tuple(2.0 for _ in timestamps)}},
    )


def _progress() -> RunProgress:
    return RunProgress(0.0, 0.02, 0.01, 1, 2, 0.5, SimulationState.RUNNING)


if __name__ == "__main__":
    unittest.main()
