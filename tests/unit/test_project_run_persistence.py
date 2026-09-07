import re
import unittest

from farcel.application.project_run_persistence import ProjectRunPersistenceService
from farcel.contracts import (
    EngineError,
    ErrorCode,
    GraphSimulationConfig,
    GraphSimulationResult,
    ModelAsset,
    ModelNode,
    ProjectRunRecord,
    SimulationCase,
    SimulationGraph,
    SimulationProject,
    SimulationState,
)


class _ArtifactRepository:
    def __init__(self, log: list[str], *, error: EngineError | None = None) -> None:
        self.log = log
        self.error = error
        self.artifacts = []

    def save(self, project_root, artifact):
        self.log.append("artifact-save")
        if self.error is not None:
            raise self.error
        self.artifacts.append(artifact)
        return f"results/{artifact.run_id}.json"


class _ProjectRepository:
    def __init__(self, log: list[str], *, error: EngineError | None = None) -> None:
        self.log = log
        self.error = error
        self.saved = []

    def save(self, project_root, project) -> None:
        self.log.append("project-save")
        if self.error is not None:
            raise self.error
        self.saved.append(project)

    def load(self, project_root):
        raise AssertionError("load is outside persistence service scope")


class ProjectRunPersistenceTests(unittest.TestCase):
    def test_success_persists_artifact_before_project_and_returns_new_history(self) -> None:
        log: list[str] = []
        artifact_repository = _ArtifactRepository(log)
        project_repository = _ProjectRepository(log)
        project = _project(history=(_record("older"),))
        result = _result()
        service = ProjectRunPersistenceService(project_repository, artifact_repository, lambda: "run-1")

        updated, record = service.persist_run("root", project, "case", result)

        self.assertEqual(log, ["artifact-save", "project-save"])
        self.assertEqual(
            record,
            ProjectRunRecord("run-1", "case", SimulationState.COMPLETED, 0.02, 2, "results/run-1.json"),
        )
        self.assertIsNot(updated, project)
        self.assertEqual(tuple(item.run_id for item in project.run_history), ("older",))
        self.assertEqual(tuple(item.run_id for item in updated.run_history), ("older", "run-1"))
        self.assertIs(project_repository.saved[0], updated)
        artifact = artifact_repository.artifacts[0]
        self.assertEqual((artifact.run_id, artifact.project_id, artifact.result), ("run-1", project.project_id, result))
        self.assertEqual(artifact.case_snapshot.graph.nodes[0].model_path, "models/plant.fmu")
        self.assertEqual(tuple(item.relative_path for item in artifact.asset_snapshots), ("models/plant.fmu",))

    def test_duplicate_generated_run_id_blocks_both_repositories(self) -> None:
        log: list[str] = []
        service = ProjectRunPersistenceService(
            _ProjectRepository(log), _ArtifactRepository(log), lambda: "existing"
        )

        with self.assertRaises(EngineError) as raised:
            service.persist_run("root", _project(history=(_record("existing"),)), "case", _result())

        self.assertIs(raised.exception.code, ErrorCode.CONFIG_ERROR)
        self.assertEqual(raised.exception.details["issues"][0]["code"], "DUPLICATE_RUN_ID")
        self.assertEqual(log, [])

    def test_artifact_failure_does_not_save_project_or_change_input_history(self) -> None:
        log: list[str] = []
        artifact_error = EngineError(ErrorCode.PROJECT_FORMAT_ERROR, "bad artifact")
        project = _project()
        service = ProjectRunPersistenceService(
            _ProjectRepository(log), _ArtifactRepository(log, error=artifact_error), lambda: "run-1"
        )

        with self.assertRaises(EngineError) as raised:
            service.persist_run("root", project, "case", _result())

        self.assertIs(raised.exception, artifact_error)
        self.assertEqual(log, ["artifact-save"])
        self.assertEqual(project.run_history, ())

    def test_project_failure_preserves_error_details_and_reports_orphan_artifact(self) -> None:
        log: list[str] = []
        original_error = EngineError(
            ErrorCode.PROJECT_IO_ERROR, "save failed", {"path": "project.json", "diagnostic": "disk full"}
        )
        service = ProjectRunPersistenceService(
            _ProjectRepository(log, error=original_error), _ArtifactRepository(log), lambda: "run-failed"
        )

        with self.assertRaises(EngineError) as raised:
            service.persist_run("root", _project(), "case", _result())

        self.assertIs(raised.exception.code, ErrorCode.PROJECT_IO_ERROR)
        self.assertEqual(log, ["artifact-save", "project-save"])
        self.assertEqual(
            raised.exception.details,
            {
                "path": "project.json", "diagnostic": "disk full", "run_id": "run-failed",
                "orphan_result_path": "results/run-failed.json", "history_committed": False,
            },
        )

    def test_stopped_result_is_persisted_in_history(self) -> None:
        log: list[str] = []
        service = ProjectRunPersistenceService(
            _ProjectRepository(log), _ArtifactRepository(log), lambda: "stopped"
        )

        updated, record = service.persist_run("root", _project(), "case", _result(SimulationState.STOPPED))

        self.assertIs(record.completion_state, SimulationState.STOPPED)
        self.assertEqual(updated.run_history, (record,))
        self.assertEqual(log, ["artifact-save", "project-save"])

    def test_default_run_id_is_nonempty_and_portable(self) -> None:
        log: list[str] = []
        artifact_repository = _ArtifactRepository(log)
        service = ProjectRunPersistenceService(_ProjectRepository(log), artifact_repository)

        service.persist_run("root", _project(), "case", _result())

        self.assertRegex(artifact_repository.artifacts[0].run_id, r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _project(*, history=()) -> SimulationProject:
    return SimulationProject(
        "project", "Demo", (ModelAsset("plant", "Plant", "models/plant.fmu", "a" * 64),),
        (SimulationCase("case", "Case", SimulationGraph(nodes=(ModelNode("plant", "models/plant.fmu"),)), GraphSimulationConfig(stop_time=0.02, communication_step=0.01)),), history,
    )


def _record(run_id: str) -> ProjectRunRecord:
    return ProjectRunRecord(run_id, "case", SimulationState.COMPLETED, 0.01, 1, f"results/{run_id}.json")


def _result(state: SimulationState = SimulationState.COMPLETED) -> GraphSimulationResult:
    return GraphSimulationResult(0.0, 0.02, 0.01, 2, 0.02, state, (0.0, 0.01, 0.02), {"plant": {"x0": (1.0, 2.0, 3.0)}})
