import json
import math
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from farcel.contracts import (
    EngineError,
    ErrorCode,
    GraphSimulationConfig,
    GraphSimulationResult,
    ModelNode,
    ProjectAssetSnapshot,
    ProjectRunArtifact,
    SimulationCase,
    SimulationGraph,
    SimulationState,
)
from farcel.infrastructure.project import (
    JsonProjectRunArtifactCodec,
    LocalJsonProjectRunArtifactRepository,
)


class ProjectRunArtifactRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "Project"
        self.repository = LocalJsonProjectRunArtifactRepository()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_save_load_and_content_are_canonical(self) -> None:
        artifact = _artifact("run-123")

        result_path = self.repository.save(self.root, artifact)
        text = (self.root / result_path).read_text(encoding="utf-8")
        loaded = self.repository.load(self.root, result_path)

        self.assertEqual(result_path, "results/run-123.json")
        self.assertTrue(text.endswith("\n"))
        self.assertIn('"$farcel_float": "nan"', text)
        json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        self.assertEqual(loaded.run_id, artifact.run_id)
        self.assertEqual(loaded.case_snapshot, artifact.case_snapshot)
        self.assertEqual(loaded.result.node_outputs["plant"]["x0"], (1.0, 2.0, 3.0))
        self.assertTrue(math.isnan(loaded.result.node_outputs["plant"]["non_finite"][0]))
        self.assertEqual(tuple((self.root / "results").glob(".result-*.tmp")), ())

    def test_duplicate_save_preserves_existing_artifact(self) -> None:
        self.repository.save(self.root, _artifact("same", project_id="first"))
        target = self.root / "results" / "same.json"
        original = target.read_bytes()

        with self.assertRaises(EngineError) as raised:
            self.repository.save(self.root, _artifact("same", project_id="second"))

        self.assertIs(raised.exception.code, ErrorCode.PROJECT_IO_ERROR)
        self.assertEqual(target.read_bytes(), original)

    def test_load_errors_and_path_identity_mismatch(self) -> None:
        self.root.joinpath("results").mkdir(parents=True)
        (self.root / "results" / "malformed.json").write_text("{", encoding="utf-8")
        cases = (("results/malformed.json", ErrorCode.PROJECT_FORMAT_ERROR), ("results/missing.json", ErrorCode.PROJECT_IO_ERROR))
        for result_path, code in cases:
            with self.subTest(result_path=result_path):
                with self.assertRaises(EngineError) as raised:
                    self.repository.load(self.root, result_path)
                self.assertIs(raised.exception.code, code)

        (self.root / "results" / "run-A.json").write_text(
            JsonProjectRunArtifactCodec().dumps(_artifact("run-B")), encoding="utf-8"
        )
        with self.assertRaises(EngineError) as raised:
            self.repository.load(self.root, "results/run-A.json")
        self.assertIs(raised.exception.code, ErrorCode.PROJECT_FORMAT_ERROR)

    def test_unsafe_run_ids_and_result_paths_are_format_errors(self) -> None:
        unsafe_ids = ("", " ", ".", "..", "../escape", "foo/bar", "foo\\bar", "C:/bad", "C:\\bad", "/absolute", "bad:name", "bad?", "bad*")
        for run_id in unsafe_ids:
            with self.subTest(run_id=run_id):
                with self.assertRaises(EngineError) as raised:
                    self.repository.save(self.root, _artifact(run_id))
                self.assertIs(raised.exception.code, ErrorCode.PROJECT_FORMAT_ERROR)

        unsafe_paths = ("../outside.json", "results/../outside.json", "/absolute.json", "C:/outside.json", "results\\run.json", "other/run.json", "results/sub/run.json", "results/run.txt", "results/.json")
        for result_path in unsafe_paths:
            with self.subTest(result_path=result_path):
                with self.assertRaises(EngineError) as raised:
                    self.repository.load(self.root, result_path)
                self.assertIs(raised.exception.code, ErrorCode.PROJECT_FORMAT_ERROR)

    def test_relocation_and_project_json_isolation(self) -> None:
        location_a = self.root.parent / "LocationA" / "Project"
        location_b = self.root.parent / "LocationB" / "Project"
        location_a.mkdir(parents=True)
        sentinel = b'{"project":"unchanged"}\n'
        (location_a / "project.json").write_bytes(sentinel)
        result_path = self.repository.save(location_a, _artifact("run-1"))

        shutil.copytree(location_a, location_b)
        loaded = self.repository.load(location_b, result_path)

        self.assertEqual(loaded.run_id, "run-1")
        self.assertEqual((location_a / "project.json").read_bytes(), sentinel)
        self.assertNotIn(str(location_a.resolve()), (location_b / result_path).read_text(encoding="utf-8"))

    def test_encoding_and_filesystem_failures_leave_no_new_artifact_or_temp(self) -> None:
        bad = _artifact_with_bad_sample("bad")
        with self.assertRaises(EngineError) as raised:
            self.repository.save(self.root, bad)
        self.assertIs(raised.exception.code, ErrorCode.PROJECT_FORMAT_ERROR)
        self.assertFalse((self.root / "results" / "bad.json").exists())
        self.assertFalse((self.root / "results").exists())

        with patch(
            "farcel.infrastructure.project.result_repository.tempfile.NamedTemporaryFile",
            side_effect=OSError("write blocked"),
        ):
            with self.assertRaises(EngineError) as raised:
                self.repository.save(self.root, _artifact("blocked"))
        self.assertIs(raised.exception.code, ErrorCode.PROJECT_IO_ERROR)
        self.assertFalse((self.root / "results" / "blocked.json").exists())
        self.assertEqual(tuple((self.root / "results").glob(".result-*.tmp")), ())

        with patch(
            "farcel.infrastructure.project.result_repository.os.replace",
            side_effect=OSError("commit blocked"),
        ):
            with self.assertRaises(EngineError) as raised:
                self.repository.save(self.root, _artifact("replace-blocked"))
        self.assertIs(raised.exception.code, ErrorCode.PROJECT_IO_ERROR)
        self.assertFalse((self.root / "results" / "replace-blocked.json").exists())
        self.assertEqual(tuple((self.root / "results").glob(".result-*.tmp")), ())

    def test_symlinked_results_directory_cannot_escape_project_root(self) -> None:
        self.root.mkdir()
        outside = self.root.parent / "Outside"
        outside.mkdir()
        try:
            os.symlink(outside, self.root / "results", target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")

        with self.assertRaises(EngineError) as raised:
            self.repository.save(self.root, _artifact("escape"))

        self.assertIs(raised.exception.code, ErrorCode.PROJECT_FORMAT_ERROR)
        self.assertFalse((outside / "escape.json").exists())


def _artifact(run_id: str, *, project_id: str = "project") -> ProjectRunArtifact:
    case = SimulationCase(
        "case", "Case", SimulationGraph(nodes=(ModelNode("plant", "models/plant.fmu"),)), GraphSimulationConfig(stop_time=0.02, communication_step=0.01)
    )
    result = GraphSimulationResult(
        0.0, 0.02, 0.01, 2, 0.02, SimulationState.COMPLETED, (0.0, 0.01, 0.02),
        {"plant": {"x0": (1.0, 2.0, 3.0), "non_finite": (float("nan"), float("inf"), float("-inf"))}},
    )
    return ProjectRunArtifact(run_id, project_id, case, (ProjectAssetSnapshot("plant", "models/plant.fmu", "a" * 64),), result)


def _artifact_with_bad_sample(run_id: str) -> ProjectRunArtifact:
    artifact = _artifact(run_id)
    result = GraphSimulationResult(
        artifact.result.start_time, artifact.result.stop_time, artifact.result.step_size,
        artifact.result.completed_steps, artifact.result.final_time, artifact.result.completion_state,
        artifact.result.timestamps, {"plant": {"bad": (object(), object(), object())}},
    )
    return ProjectRunArtifact(artifact.run_id, artifact.project_id, artifact.case_snapshot, artifact.asset_snapshots, result)
