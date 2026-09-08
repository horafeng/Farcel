from dataclasses import replace
from pathlib import Path
import unittest

from farcel.application.engine import FarcelEngine
from farcel.contracts import (
    EngineError,
    ErrorCode,
    GraphSimulationConfig,
    GraphSimulationResult,
    ModelNode,
    ProjectRunArtifact,
    ProjectRunRecord,
    SimulationCase,
    SimulationGraph,
    SimulationProject,
    SimulationState,
)


class _Importer:
    def __init__(self) -> None:
        self.load_calls: list[Path] = []

    def load(self, path: Path):
        self.load_calls.append(path)
        raise AssertionError("historical load must not import an FMU")


class _ArtifactRepository:
    def __init__(
        self,
        artifact: ProjectRunArtifact | None = None,
        error: EngineError | None = None,
    ) -> None:
        self.artifact = artifact
        self.error = error
        self.load_calls: list[tuple[Path, str]] = []

    def load(self, project_root: Path, result_path: str) -> ProjectRunArtifact:
        self.load_calls.append((project_root, result_path))
        if self.error is not None:
            raise self.error
        assert self.artifact is not None
        return self.artifact


class _ProjectRepository:
    def __init__(self, project: SimulationProject) -> None:
        self.project = project

    def load(self, project_root: Path) -> SimulationProject:
        return self.project

    def save(self, project_root: Path, project: SimulationProject) -> None:
        pass


class ProjectRunBackendApiTests(unittest.TestCase):
    def test_exact_history_lookup_delegates_to_artifact_repository(self) -> None:
        record_a = _record("run-A")
        record_b = _record("run-B")
        artifact = _artifact("run-B")
        repository = _ArtifactRepository(artifact)
        engine = FarcelEngine(_Importer(), project_run_artifact_repository=repository)

        loaded = engine.load_project_run("project-root", _project(record_a, record_b), "run-B")

        self.assertIs(loaded, artifact)
        self.assertEqual(repository.load_calls, [(Path("project-root"), "results/run-B.json")])

    def test_unknown_and_duplicate_run_ids_do_not_call_repository(self) -> None:
        for project, run_id, expected_code in (
            (_project(_record("run-A")), "missing", "UNKNOWN_RUN_ID"),
            (_project(_record("run-A"), _record("run-A")), "run-A", "DUPLICATE_RUN_ID"),
        ):
            with self.subTest(expected_code=expected_code):
                repository = _ArtifactRepository(_artifact("run-A"))
                engine = FarcelEngine(
                    _Importer(), project_run_artifact_repository=repository
                )

                with self.assertRaises(EngineError) as raised:
                    engine.load_project_run("project-root", project, run_id)

                self.assertEqual(raised.exception.code, ErrorCode.CONFIG_ERROR)
                self.assertEqual(raised.exception.details["issues"][0]["field"], "run_id")
                self.assertEqual(raised.exception.details["issues"][0]["code"], expected_code)
                self.assertEqual(repository.load_calls, [])

    def test_noncanonical_record_path_is_rejected_before_repository_load(self) -> None:
        repository = _ArtifactRepository(_artifact("run-A"))
        record = replace(_record("run-A"), result_path="results/run-B.json")
        engine = FarcelEngine(_Importer(), project_run_artifact_repository=repository)

        with self.assertRaises(EngineError) as raised:
            engine.load_project_run("project-root", _project(record), "run-A")

        self.assertEqual(raised.exception.code, ErrorCode.PROJECT_FORMAT_ERROR)
        self.assertEqual(raised.exception.details["field"], "result_path")
        self.assertEqual(repository.load_calls, [])

    def test_artifact_history_consistency_mismatches_are_format_errors(self) -> None:
        record = _record("run-A")
        expected_fields = {
            "run_id": "artifact.run_id",
            "project_id": "artifact.project_id",
            "case_id": "artifact.case_snapshot.case_id",
            "completion_state": "artifact.result.completion_state",
            "final_time": "artifact.result.final_time",
            "completed_steps": "artifact.result.completed_steps",
        }
        mismatches = {
            "run_id": replace(_artifact("run-A"), run_id="run-B"),
            "project_id": replace(_artifact("run-A"), project_id="other-project"),
            "case_id": replace(
                _artifact("run-A"),
                case_snapshot=replace(_artifact("run-A").case_snapshot, case_id="other-case"),
            ),
            "completion_state": replace(
                _artifact("run-A"),
                result=replace(_artifact("run-A").result, completion_state=SimulationState.STOPPED),
            ),
            "final_time": replace(
                _artifact("run-A"),
                result=replace(
                    _artifact("run-A").result,
                    final_time=0.015,
                    timestamps=(0.0, 0.01, 0.015),
                ),
            ),
            "completed_steps": replace(
                _artifact("run-A"), result=replace(_artifact("run-A").result, completed_steps=3)
            ),
        }

        for name, artifact in mismatches.items():
            with self.subTest(name=name):
                repository = _ArtifactRepository(artifact)
                engine = FarcelEngine(
                    _Importer(), project_run_artifact_repository=repository
                )

                with self.assertRaises(EngineError) as raised:
                    engine.load_project_run("project-root", _project(record), "run-A")

                self.assertEqual(raised.exception.code, ErrorCode.PROJECT_FORMAT_ERROR)
                self.assertEqual(raised.exception.details["field"], expected_fields[name])

    def test_repository_errors_propagate_unchanged(self) -> None:
        for error in (
            EngineError(ErrorCode.PROJECT_IO_ERROR, "result artifact missing", {"path": "results/run-A.json"}),
            EngineError(ErrorCode.PROJECT_FORMAT_ERROR, "result artifact malformed", {"diagnostic": "bad JSON"}),
        ):
            with self.subTest(error_code=error.code):
                repository = _ArtifactRepository(error=error)
                engine = FarcelEngine(
                    _Importer(), project_run_artifact_repository=repository
                )

                with self.assertRaises(EngineError) as raised:
                    engine.load_project_run("project-root", _project(_record("run-A")), "run-A")

                self.assertIs(raised.exception, error)

    def test_missing_repository_is_not_implemented(self) -> None:
        engine = FarcelEngine(_Importer())

        with self.assertRaises(EngineError) as raised:
            engine.load_project_run("project-root", _project(_record("run-A")), "run-A")

        self.assertEqual(raised.exception.code, ErrorCode.NOT_IMPLEMENTED)

    def test_historical_load_ignores_invalid_current_project_state(self) -> None:
        importer = _Importer()
        artifact = _artifact("run-A")
        repository = _ArtifactRepository(artifact)
        project = SimulationProject("project", "Current project", run_history=(_record("run-A"),))
        engine = FarcelEngine(importer, project_run_artifact_repository=repository)

        loaded = engine.load_project_run("project-root", project, "run-A")

        self.assertIs(loaded, artifact)
        self.assertEqual(importer.load_calls, [])
        self.assertEqual(project.simulation_cases, ())

    def test_legacy_five_and_5_5a_six_argument_construction_remain_compatible(self) -> None:
        importer = _Importer()
        legacy = FarcelEngine(importer, None, None, None, None)
        project = _project(_record("run-A"))
        phase_5_5a = FarcelEngine(
            importer,
            None,
            None,
            None,
            None,
            _ProjectRepository(project),
        )

        self.assertTrue(legacy.validate_project("project-root", SimulationProject("project", "Demo")).is_valid)
        self.assertEqual(phase_5_5a.open_project("project-root"), project)


def _project(*records: ProjectRunRecord) -> SimulationProject:
    return SimulationProject("project", "Project", run_history=records)


def _record(run_id: str) -> ProjectRunRecord:
    return ProjectRunRecord(
        run_id,
        "case",
        SimulationState.COMPLETED,
        0.02,
        2,
        f"results/{run_id}.json",
    )


def _artifact(run_id: str) -> ProjectRunArtifact:
    case = SimulationCase(
        "case",
        "Historical case",
        SimulationGraph(nodes=(ModelNode("plant", "models/plant.fmu"),)),
        GraphSimulationConfig(stop_time=0.02, communication_step=0.01),
    )
    result = GraphSimulationResult(
        0.0,
        0.02,
        0.01,
        2,
        0.02,
        SimulationState.COMPLETED,
        (0.0, 0.01, 0.02),
        {"plant": {"x0": (2.0, 1.0, 0.5)}},
    )
    return ProjectRunArtifact(run_id, "project", case, (), result)
