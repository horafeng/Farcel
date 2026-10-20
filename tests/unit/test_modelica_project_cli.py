from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import patch

from farcel.cli import main
from farcel.contracts import (
    EngineError,
    ErrorCode,
    ModelAsset,
    ModelicaBuildProvenance,
    ModelicaSourceSnapshot,
    ModelicaStagedBuild,
    ProjectModelicaCompilation,
    ProjectSchemaMigrationResult,
    SimulationProject,
)


_BUILD_ID = "0" * 32
_SOURCE_SHA256 = "a" * 64
_FMU_SHA256 = "b" * 64


def _provenance(build_id: str = _BUILD_ID, asset_id: str = "modelica-build-0") -> ModelicaBuildProvenance:
    return ModelicaBuildProvenance(
        build_id=build_id,
        source_snapshot=ModelicaSourceSnapshot(
            f"modelica-builds/build-{build_id}/source/model.mo", _SOURCE_SHA256
        ),
        model_name="示例.FirstOrder",
        compiler_name="OpenModelica",
        compiler_version="1.26.3",
        fmi_version="2.0",
        fmu_type="cs",
        fmu_asset_id=asset_id,
    )


def _asset(build_id: str = _BUILD_ID, asset_id: str = "modelica-build-0") -> ModelAsset:
    return ModelAsset(
        asset_id,
        "示例 FirstOrder",
        f"modelica-builds/build-{build_id}/model/model.fmu",
        _FMU_SHA256,
    )


class _Backend:
    def __init__(self) -> None:
        self.asset = _asset()
        self.provenance = _provenance()
        self.project = SimulationProject(
            "project-001",
            "测试 Project",
            (self.asset,),
            schema_version="1.1",
            modelica_build_provenance=(self.provenance,),
        )
        self.migration = ProjectSchemaMigrationResult(
            self.project,
            "project.json.1.0-backup-0001.json",
        )
        self.registration = ProjectModelicaCompilation(
            self.project,
            ModelicaStagedBuild(self.asset, self.provenance),
        )
        self.opened_paths: list[object] = []
        self.migration_paths: list[object] = []
        self.staging_requests: list[object] = []
        self.listed_projects: list[object] = []
        self.error: EngineError | None = None

    def open_project(self, project_root):
        self.opened_paths.append(project_root)
        if self.error is not None:
            raise self.error
        return self.project

    def migrate_project_schema(self, project_root):
        self.migration_paths.append(project_root)
        if self.error is not None:
            raise self.error
        return self.migration

    def compile_project_modelica(self, project, request):
        self.staging_requests.append((project, request))
        if self.error is not None:
            raise self.error
        return self.registration

    def list_project_modelica_builds(self, project):
        self.listed_projects.append(project)
        if self.error is not None:
            raise self.error
        return project.modelica_build_provenance

    def compile_modelica(self, request):
        raise AssertionError("Project CLI 不得调用独立 compile_modelica()")

    def save_project(self, project_root, project):
        raise AssertionError("Project CLI 不得重复调用 save_project()")


class ModelicaProjectCliTests(unittest.TestCase):
    def test_project_nested_help(self) -> None:
        for arguments in (
            ("modelica", "project", "--help"),
            ("modelica", "project", "migrate", "--help"),
            ("modelica", "project", "compile", "--help"),
            ("modelica", "project", "list", "--help"),
        ):
            stdout = StringIO()
            with self.assertRaises(SystemExit) as raised, redirect_stdout(stdout):
                main(arguments)
            self.assertEqual(0, raised.exception.code)
            self.assertIn("usage:", stdout.getvalue())

    def test_migrate_delegates_and_emits_plain_and_json_results(self) -> None:
        backend = _Backend()
        with patch("farcel.cli.create_backend", return_value=backend):
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = main(("modelica", "project", "migrate", "--project-root", "项目 空格"))
            self.assertEqual(0, code)
            self.assertEqual(["项目 空格"], backend.migration_paths)
            self.assertIn("迁移完成: 是", stdout.getvalue())

            stdout = StringIO()
            with redirect_stdout(stdout):
                code = main(("modelica", "project", "migrate", "--project-root", "项目 空格", "--json"))
        self.assertEqual(0, code)
        payload = json.loads(stdout.getvalue())
        self.assertEqual("项目 空格", payload["project_root"])
        self.assertEqual("1.1", payload["schema_version"])
        self.assertEqual("project.json.1.0-backup-0001.json", payload["backup_relative_path"])

    def test_migrate_rejection_uses_stable_error_protocol(self) -> None:
        backend = _Backend()
        backend.error = EngineError(ErrorCode.CONFIG_ERROR, "仅支持显式迁移 Project schema 1.0。")
        stdout, stderr = StringIO(), StringIO()
        with patch("farcel.cli.create_backend", return_value=backend), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(("modelica", "project", "migrate", "--project-root", "project", "--json"))
        self.assertEqual(1, code)
        self.assertEqual("", stdout.getvalue())
        self.assertEqual("CONFIG_ERROR", json.loads(stderr.getvalue())["error"]["code"])

    def test_compile_constructs_staging_request_and_uses_only_project_api(self) -> None:
        backend = _Backend()
        arguments = (
            "modelica", "project", "compile", "--project-root", "项目 目录",
            "--source", "模型 空格/First Order.mo", "--model", "示例.FirstOrder",
            "--omc", "C:/Program Files/OpenModelica/bin/omc.exe", "--timeout", "42.5",
        )
        with patch("farcel.cli.create_backend", return_value=backend):
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = main(arguments)
        self.assertEqual(0, code)
        self.assertEqual(["项目 目录"], backend.opened_paths)
        project, request = backend.staging_requests[-1]
        self.assertIs(backend.project, project)
        self.assertEqual("项目 目录", request.project_root)
        self.assertEqual("模型 空格/First Order.mo", request.source_path)
        self.assertEqual("示例.FirstOrder", request.model_name)
        self.assertEqual("C:/Program Files/OpenModelica/bin/omc.exe", request.compiler_path)
        self.assertEqual(42.5, request.timeout_seconds)
        self.assertIn("编译并登记完成: 是", stdout.getvalue())

    def test_compile_json_uses_only_registered_public_data(self) -> None:
        backend = _Backend()
        with patch("farcel.cli.create_backend", return_value=backend):
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = main((
                    "modelica", "project", "compile", "--project-root", "project",
                    "--source", "model.mo", "--model", "示例.FirstOrder", "--json",
                ))
        self.assertEqual(0, code)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["success"])
        self.assertEqual(_BUILD_ID, payload["build_id"])
        self.assertEqual(backend.asset.relative_path, payload["fmu_relative_path"])
        self.assertEqual(_FMU_SHA256, payload["fmu_sha256"])
        self.assertEqual(_SOURCE_SHA256, payload["source_sha256"])
        self.assertEqual("OpenModelica", payload["compiler_name"])

    def test_compile_project_errors_preserve_conflict_and_recovery_details(self) -> None:
        backend = _Backend()
        backend.error = EngineError(
            ErrorCode.PROJECT_CONFLICT,
            "Modelica 构建包已验证，但 project.json 登记未完成。",
            {
                "stage": "project_persistence",
                "unregistered_build": {
                    "build_id": _BUILD_ID,
                    "fmu_relative_path": backend.asset.relative_path,
                },
            },
        )
        stdout, stderr = StringIO(), StringIO()
        with patch("farcel.cli.create_backend", return_value=backend), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main((
                "modelica", "project", "compile", "--project-root", "project",
                "--source", "model.mo", "--model", "M", "--json",
            ))
        self.assertEqual(1, code)
        self.assertEqual("", stdout.getvalue())
        payload = json.loads(stderr.getvalue())
        self.assertEqual("PROJECT_CONFLICT", payload["error"]["code"])
        self.assertEqual(_BUILD_ID, payload["error"]["details"]["unregistered_build"]["build_id"])

    def test_list_handles_empty_and_multiple_records_without_compilation(self) -> None:
        backend = _Backend()
        backend.project = SimulationProject("empty", "空 Project", schema_version="1.0")
        with patch("farcel.cli.create_backend", return_value=backend):
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = main(("modelica", "project", "list", "--project-root", "project", "--json"))
        self.assertEqual(0, code)
        self.assertEqual([], json.loads(stdout.getvalue())["builds"])

        second_asset = _asset("1" * 32, "modelica-build-1")
        second_provenance = _provenance("1" * 32, "modelica-build-1")
        backend.project = SimulationProject(
            "project-001", "多构建", (backend.asset, second_asset), schema_version="1.1",
            modelica_build_provenance=(backend.provenance, second_provenance),
        )
        with patch("farcel.cli.create_backend", return_value=backend):
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = main(("modelica", "project", "list", "--project-root", "project", "--json"))
        self.assertEqual(0, code)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(2, payload["count"])
        self.assertEqual(second_asset.relative_path, payload["builds"][1]["fmu_relative_path"])

    def test_list_error_json_and_plain_exit_code_are_stable(self) -> None:
        backend = _Backend()
        backend.error = EngineError(ErrorCode.PROJECT_IO_ERROR, "Project 不存在。")
        stdout, stderr = StringIO(), StringIO()
        with patch("farcel.cli.create_backend", return_value=backend), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(("modelica", "project", "list", "--project-root", "missing", "--json"))
        self.assertEqual(1, code)
        self.assertEqual("", stdout.getvalue())
        self.assertEqual("PROJECT_IO_ERROR", json.loads(stderr.getvalue())["error"]["code"])

    def test_project_commands_require_mandatory_arguments(self) -> None:
        for arguments in (
            ("modelica", "project", "migrate"),
            ("modelica", "project", "compile", "--project-root", "project"),
            ("modelica", "project", "list"),
        ):
            stderr = StringIO()
            with self.assertRaises(SystemExit) as raised, redirect_stderr(stderr):
                main(arguments)
            self.assertEqual(2, raised.exception.code)
            self.assertIn("error:", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
