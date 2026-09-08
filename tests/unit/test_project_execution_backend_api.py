from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from farcel.application.engine import FarcelEngine
from farcel.contracts import (
    EngineError,
    ErrorCode,
    GraphSimulationResult,
    ProjectRunRecord,
    SimulationProject,
    SimulationState,
)
from farcel.contracts.run_control import RunControl


class _Importer:
    def load(self, path: Path):
        raise AssertionError("public facade orchestration must not import an FMU")


class _ProjectRepository:
    def load(self, project_root: Path) -> SimulationProject:
        raise AssertionError("run_project_case must not reload project.json")

    def save(self, project_root: Path, project: SimulationProject) -> None:
        pass


class _ArtifactRepository:
    def load(self, project_root: Path, result_path: str):
        raise AssertionError("load is outside run_project_case scope")

    def save(self, project_root: Path, artifact) -> str:
        raise AssertionError("persistence fake owns this call")


class ProjectExecutionBackendApiTests(unittest.TestCase):
    def test_composes_execution_then_persistence_with_exact_arguments(self) -> None:
        project = SimulationProject("project", "Project")
        updated_project = SimulationProject("project", "Project", run_history=(_record(),))
        result = _result()
        control = RunControl()
        on_progress = lambda progress: None
        project_repository = _ProjectRepository()
        artifact_repository = _ArtifactRepository()
        engine = FarcelEngine(
            _Importer(),
            project_repository=project_repository,
            project_run_artifact_repository=artifact_repository,
        )
        project_service = Mock()
        project_service.run_case.return_value = result
        persistence = Mock()
        persistence.persist_run.return_value = (updated_project, _record())

        with patch(
            "farcel.application.engine.ProjectService", return_value=project_service
        ) as project_service_type, patch(
            "farcel.application.engine.ProjectRunPersistenceService",
            return_value=persistence,
        ) as persistence_type:
            actual = engine.run_project_case(
                "project-root",
                project,
                "case-1",
                control=control,
                on_progress=on_progress,
            )

        self.assertEqual(actual, (updated_project, _record(), result))
        project_service_type.assert_called_once_with(engine._project_validator, engine.run_graph)
        project_service.run_case.assert_called_once_with(
            Path("project-root"),
            project,
            "case-1",
            control=control,
            on_progress=on_progress,
        )
        persistence_type.assert_called_once_with(project_repository, artifact_repository)
        persistence.persist_run.assert_called_once_with(
            Path("project-root"), project, "case-1", result
        )

    def test_execution_happens_before_persistence(self) -> None:
        project = SimulationProject("project", "Project")
        result = _result()
        call_log: list[str] = []
        engine = FarcelEngine(
            _Importer(),
            project_repository=_ProjectRepository(),
            project_run_artifact_repository=_ArtifactRepository(),
        )
        project_service = Mock()
        project_service.run_case.side_effect = lambda *args, **kwargs: (
            call_log.append("project-run") or result
        )
        persistence = Mock()
        persistence.persist_run.side_effect = lambda *args: (
            call_log.append("persist-run") or (project, _record())
        )

        with patch("farcel.application.engine.ProjectService", return_value=project_service), patch(
            "farcel.application.engine.ProjectRunPersistenceService", return_value=persistence
        ):
            engine.run_project_case("root", project, "case-1")

        self.assertEqual(call_log, ["project-run", "persist-run"])

    def test_missing_repositories_fail_before_execution(self) -> None:
        project = SimulationProject("project", "Project")
        for project_repository, artifact_repository in (
            (None, _ArtifactRepository()),
            (_ProjectRepository(), None),
        ):
            with self.subTest(
                project_repository=project_repository is not None,
                artifact_repository=artifact_repository is not None,
            ):
                engine = FarcelEngine(
                    _Importer(),
                    project_repository=project_repository,
                    project_run_artifact_repository=artifact_repository,
                )
                with patch("farcel.application.engine.ProjectService") as project_service_type:
                    with self.assertRaises(EngineError) as raised:
                        engine.run_project_case("root", project, "case-1")

                self.assertEqual(raised.exception.code, ErrorCode.NOT_IMPLEMENTED)
                project_service_type.assert_not_called()

    def test_execution_and_persistence_errors_propagate_unchanged(self) -> None:
        project = SimulationProject("project", "Project")
        execution_error = EngineError(ErrorCode.CONFIG_ERROR, "invalid project")
        persistence_error = EngineError(ErrorCode.PROJECT_IO_ERROR, "artifact save failed")
        engine = FarcelEngine(
            _Importer(),
            project_repository=_ProjectRepository(),
            project_run_artifact_repository=_ArtifactRepository(),
        )
        failing_service = Mock()
        failing_service.run_case.side_effect = execution_error

        with patch("farcel.application.engine.ProjectService", return_value=failing_service), patch(
            "farcel.application.engine.ProjectRunPersistenceService"
        ) as persistence_type:
            with self.assertRaises(EngineError) as execution_raised:
                engine.run_project_case("root", project, "case-1")

        self.assertIs(execution_raised.exception, execution_error)
        persistence_type.assert_not_called()

        successful_service = Mock()
        successful_service.run_case.return_value = _result()
        failing_persistence = Mock()
        failing_persistence.persist_run.side_effect = persistence_error
        with patch("farcel.application.engine.ProjectService", return_value=successful_service), patch(
            "farcel.application.engine.ProjectRunPersistenceService",
            return_value=failing_persistence,
        ):
            with self.assertRaises(EngineError) as persistence_raised:
                engine.run_project_case("root", project, "case-1")

        self.assertIs(persistence_raised.exception, persistence_error)

    def test_stopped_result_is_persisted_and_returned_unchanged(self) -> None:
        project = SimulationProject("project", "Project")
        result = _result(SimulationState.STOPPED)
        updated_project = SimulationProject("project", "Project", run_history=(_record(SimulationState.STOPPED),))
        engine = FarcelEngine(
            _Importer(),
            project_repository=_ProjectRepository(),
            project_run_artifact_repository=_ArtifactRepository(),
        )
        project_service = Mock()
        project_service.run_case.return_value = result
        persistence = Mock()
        persistence.persist_run.return_value = (updated_project, _record(SimulationState.STOPPED))

        with patch("farcel.application.engine.ProjectService", return_value=project_service), patch(
            "farcel.application.engine.ProjectRunPersistenceService", return_value=persistence
        ):
            actual = engine.run_project_case("root", project, "case-1")

        self.assertIs(actual[2], result)
        persistence.persist_run.assert_called_once_with(Path("root"), project, "case-1", result)

    def test_five_six_and_seven_argument_construction_remain_compatible(self) -> None:
        importer = _Importer()
        project_repository = _ProjectRepository()
        artifact_repository = _ArtifactRepository()

        self.assertIsInstance(FarcelEngine(importer, None, None, None, None), FarcelEngine)
        self.assertIsInstance(
            FarcelEngine(importer, None, None, None, None, project_repository),
            FarcelEngine,
        )
        self.assertIsInstance(
            FarcelEngine(
                importer,
                None,
                None,
                None,
                None,
                project_repository,
                artifact_repository,
            ),
            FarcelEngine,
        )


def _record(state: SimulationState = SimulationState.COMPLETED) -> ProjectRunRecord:
    return ProjectRunRecord("run-1", "case-1", state, 0.02, 2, "results/run-1.json")


def _result(state: SimulationState = SimulationState.COMPLETED) -> GraphSimulationResult:
    final_time = 0.02 if state is SimulationState.COMPLETED else 0.01
    timestamps = (0.0, 0.01, 0.02) if state is SimulationState.COMPLETED else (0.0, 0.01)
    outputs = (2.0, 2.0, 2.0) if state is SimulationState.COMPLETED else (2.0, 2.0)
    return GraphSimulationResult(
        0.0,
        0.02,
        0.01,
        2,
        final_time,
        state,
        timestamps,
        {"plant": {"x0": outputs}},
    )
