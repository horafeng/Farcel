from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from farcel.application.project_modelica_service import ProjectModelicaService
from farcel.contracts import (
    EngineError,
    ErrorCode,
    ModelAsset,
    ModelicaAssetStagingRequest,
    ModelicaBuildProvenance,
    ModelicaSourceSnapshot,
    ModelicaStagedBuild,
    ProjectRevision,
    SimulationProject,
    ValidationIssue,
    ValidationReport,
)


_BUILD_ID = "a" * 32


class _Repository:
    def __init__(self, project: SimulationProject) -> None:
        self.revision = ProjectRevision(project, "b" * 64)
        self.saved: list[tuple[Path, SimulationProject, str]] = []
        self.save_error: EngineError | None = None

    def load_with_revision(self, root: Path) -> ProjectRevision:
        return self.revision

    def save_if_unchanged(
        self, root: Path, project: SimulationProject, revision: str
    ) -> None:
        self.saved.append((root, project, revision))
        if self.save_error is not None:
            raise self.save_error


class _StagingService:
    def __init__(self, result: ModelicaStagedBuild) -> None:
        self.result = result
        self.requests: list[ModelicaAssetStagingRequest] = []
        self.error: EngineError | None = None

    def stage(self, request: ModelicaAssetStagingRequest) -> ModelicaStagedBuild:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result


class _Validator:
    def __init__(self) -> None:
        self.reports: list[ValidationReport] = []
        self.projects: list[SimulationProject] = []

    def validate(self, root: Path, project: SimulationProject) -> ValidationReport:
        self.projects.append(project)
        return self.reports.pop(0) if self.reports else ValidationReport()


class ProjectModelicaServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = SimulationProject("project", "Demo", schema_version="1.1")
        self.repository = _Repository(self.project)
        self.staging = _StagingService(_staged_build())
        self.validator = _Validator()
        self.service = ProjectModelicaService(self.staging, self.repository, self.validator)
        self.request = ModelicaAssetStagingRequest(
            project_root="project-root",
            source_path="source.mo",
            model_name="Example.FirstOrder",
        )

    def test_registers_verified_build_and_preserves_existing_project_data(self) -> None:
        existing = ModelAsset("existing", "Existing", "models/existing.fmu", "c" * 64)
        project = replace(
            self.project,
            model_assets=(existing,),
        )
        self.repository.revision = replace(self.repository.revision, project=project)

        result = self.service.compile_and_register(project, self.request)

        self.assertEqual((existing, _staged_build().model_asset), result.project.model_assets)
        self.assertEqual((_staged_build().provenance,), result.project.modelica_build_provenance)
        self.assertEqual(result.staged_build, _staged_build())
        self.assertEqual(1, len(self.repository.saved))
        self.assertEqual("b" * 64, self.repository.saved[0][2])
        self.assertEqual([self.request], self.staging.requests)
        self.assertEqual((_staged_build().provenance,), self.service.list_builds(result.project))

    def test_schema_1_0_and_stale_caller_are_rejected_before_staging(self) -> None:
        with self.assertRaises(EngineError) as legacy:
            self.service.compile_and_register(
                replace(self.project, schema_version="1.0"), self.request
            )
        self.assertEqual(ErrorCode.CONFIG_ERROR, legacy.exception.code)
        self.assertEqual([], self.staging.requests)

        with self.assertRaises(EngineError) as stale:
            self.service.compile_and_register(replace(self.project, name="Old"), self.request)
        self.assertEqual(ErrorCode.PROJECT_CONFLICT, stale.exception.code)
        self.assertEqual([], self.staging.requests)

    def test_invalid_project_and_staging_failure_do_not_save(self) -> None:
        self.validator.reports.append(
            ValidationReport((ValidationIssue("name", "INVALID", "无效"),))
        )
        with self.assertRaises(EngineError) as invalid:
            self.service.compile_and_register(self.project, self.request)
        self.assertEqual(ErrorCode.CONFIG_ERROR, invalid.exception.code)
        self.assertEqual([], self.staging.requests)
        self.assertEqual([], self.repository.saved)

        self.validator.reports.clear()
        self.staging.error = EngineError(ErrorCode.MODELICA_COMPILATION_ERROR, "编译失败")
        with self.assertRaises(EngineError) as failed:
            self.service.compile_and_register(self.project, self.request)
        self.assertEqual(ErrorCode.MODELICA_COMPILATION_ERROR, failed.exception.code)
        self.assertEqual([], self.repository.saved)

    def test_persistence_failure_keeps_build_identification_in_error_details(self) -> None:
        self.repository.save_error = EngineError(ErrorCode.PROJECT_CONFLICT, "冲突")

        with self.assertRaises(EngineError) as raised:
            self.service.compile_and_register(self.project, self.request)

        self.assertEqual(ErrorCode.PROJECT_CONFLICT, raised.exception.code)
        self.assertEqual("project_persistence", raised.exception.details["stage"])
        self.assertEqual(_BUILD_ID, raised.exception.details["unregistered_build"]["build_id"])
        self.assertEqual(1, len(self.repository.saved))

    def test_post_staging_validation_failure_does_not_persist(self) -> None:
        self.validator.reports.extend(
            (
                ValidationReport(),
                ValidationReport((ValidationIssue("model_assets", "DUPLICATE_ASSET_ID", "冲突"),)),
            )
        )
        with self.assertRaises(EngineError) as raised:
            self.service.compile_and_register(self.project, self.request)
        self.assertEqual(ErrorCode.CONFIG_ERROR, raised.exception.code)
        self.assertEqual([], self.repository.saved)


def _staged_build() -> ModelicaStagedBuild:
    asset = ModelAsset(
        f"modelica-build-{_BUILD_ID}",
        "Example.FirstOrder",
        f"modelica-builds/build-{_BUILD_ID}/model/model.fmu",
        "d" * 64,
    )
    provenance = ModelicaBuildProvenance(
        _BUILD_ID,
        ModelicaSourceSnapshot(
            f"modelica-builds/build-{_BUILD_ID}/source/model.mo", "e" * 64
        ),
        "Example.FirstOrder",
        "TestCompiler",
        "1.0",
        "2.0",
        "cs",
        asset.asset_id,
    )
    return ModelicaStagedBuild(asset, provenance)
