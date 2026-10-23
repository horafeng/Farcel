from __future__ import annotations

import hashlib
import json
from pathlib import Path
from dataclasses import replace
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from farcel.application.project_schema_migration import ProjectSchemaMigrationService
from farcel.application.project_validation import ProjectValidator
from farcel.contracts import (
    EngineError,
    ErrorCode,
    InterfaceCapability,
    InterfaceType,
    LATEST_PROJECT_SCHEMA_VERSION,
    ModelAsset,
    ModelicaBuildProvenance,
    ModelicaSourceSnapshot,
    ProjectRunRecord,
    ModelMetadata,
    ModelNode,
    SimulationCase,
    SimulationGraph,
    SimulationProject,
    SimulationState,
)
from farcel.contracts.graph import GraphSimulationConfig
from farcel.infrastructure.project import LocalJsonProjectRepository


_BUILD_ID = "a" * 32


class ProjectSchema11Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = TemporaryDirectory()
        self.root = Path(self._temporary_directory.name) / "项目"
        self.root.mkdir()
        self.repository = LocalJsonProjectRepository()

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_schema_1_0_remains_canonical_and_rejects_provenance_on_save(self) -> None:
        project = SimulationProject("p", "旧项目")
        self.repository.save(self.root, project)
        document = _read_document(self.root)
        self.assertEqual("1.0", document["schema_version"])
        self.assertNotIn("modelica_build_provenance", document)
        self.assertEqual(project, self.repository.load(self.root))

        with self.assertRaises(EngineError) as raised:
            self.repository.save(
                self.root,
                SimulationProject(
                    "p", "旧项目", schema_version="1.0", modelica_build_provenance=(_provenance(),)
                ),
            )
        self.assertIs(ErrorCode.PROJECT_FORMAT_ERROR, raised.exception.code)

    def test_schema_1_1_round_trips_empty_and_nonempty_provenance_with_utf8(self) -> None:
        empty = SimulationProject("p", "空来源", schema_version="1.1")
        self.repository.save(self.root, empty)
        self.assertEqual(empty, self.repository.load(self.root))

        project = _persistable_project(self.root)
        self.repository.save(self.root, project)
        document = _read_document(self.root)
        self.assertEqual("1.1", document["schema_version"])
        self.assertEqual([_BUILD_ID], [item["build_id"] for item in document["modelica_build_provenance"]])
        self.assertEqual(project, self.repository.load(self.root))

    def test_conditional_save_rejects_stale_revision_without_overwriting(self) -> None:
        original = SimulationProject("p", "初始", schema_version="1.1")
        self.repository.save(self.root, original)
        revision = self.repository.load_with_revision(self.root)
        newer = replace(original, name="最新")
        self.repository.save(self.root, newer)

        with self.assertRaises(EngineError) as raised:
            self.repository.save_if_unchanged(
                self.root,
                replace(original, name="陈旧写入"),
                revision.document_sha256,
            )

        self.assertIs(ErrorCode.PROJECT_CONFLICT, raised.exception.code)
        self.assertEqual(newer, self.repository.load(self.root))

    def test_strict_schema_and_fields_are_rejected(self) -> None:
        self.repository.save(self.root, SimulationProject("p", "Demo", schema_version="1.1"))
        document = _read_document(self.root)
        del document["modelica_build_provenance"]
        _write_document(self.root, document)
        with self.assertRaises(EngineError) as raised:
            self.repository.load(self.root)
        self.assertIs(ErrorCode.PROJECT_FORMAT_ERROR, raised.exception.code)

        self.repository.save(self.root, SimulationProject("p", "Demo", schema_version="1.1"))
        document = _read_document(self.root)
        document["unknown"] = True
        _write_document(self.root, document)
        with self.assertRaises(EngineError) as raised:
            self.repository.load(self.root)
        self.assertIs(ErrorCode.PROJECT_FORMAT_ERROR, raised.exception.code)

    def test_schema_1_0_document_with_1_1_field_is_rejected(self) -> None:
        self.repository.save(self.root, SimulationProject("p", "旧项目"))
        document = _read_document(self.root)
        document["modelica_build_provenance"] = []
        _write_document(self.root, document)
        with self.assertRaises(EngineError) as raised:
            self.repository.load(self.root)
        self.assertIs(ErrorCode.PROJECT_FORMAT_ERROR, raised.exception.code)

    def test_project_validator_checks_provenance_files_association_and_hashes(self) -> None:
        project = _persistable_project(self.root)
        report = ProjectValidator(_NoImport()).validate(self.root, project)
        self.assertTrue(report.is_valid)

        source = self.root / project.modelica_build_provenance[0].source_snapshot.relative_path
        source.write_text("changed", encoding="utf-8")
        report = ProjectValidator(_NoImport()).validate(self.root, project)
        self.assertIn("SOURCE_SNAPSHOT_CHECKSUM_MISMATCH", {issue.code for issue in report.issues})

    def test_validator_rejects_source_escape_missing_asset_mismatch_and_relocation(self) -> None:
        project = _persistable_project(self.root)
        provenance = project.modelica_build_provenance[0]
        escaped = replace(
            provenance,
            source_snapshot=replace(provenance.source_snapshot, relative_path="../escape.mo"),
        )
        report = ProjectValidator(_NoImport()).validate(
            self.root,
            replace(project, modelica_build_provenance=(escaped,)),
        )
        self.assertIn("PROJECT_PATH_INVALID", {issue.code for issue in report.issues})

        source = self.root / provenance.source_snapshot.relative_path
        source.unlink()
        report = ProjectValidator(_NoImport()).validate(self.root, project)
        self.assertIn("SOURCE_SNAPSHOT_MISSING_OR_UNSAFE", {issue.code for issue in report.issues})

        project = _persistable_project(self.root)
        fmu = self.root / project.model_assets[0].relative_path
        fmu.write_bytes(b"corrupt")
        report = ProjectValidator(_NoImport()).validate(self.root, project)
        self.assertIn("ASSET_CHECKSUM_MISMATCH", {issue.code for issue in report.issues})

        fmu.unlink()
        report = ProjectValidator(_NoImport()).validate(self.root, project)
        self.assertIn("ASSET_MISSING", {issue.code for issue in report.issues})

        project = _persistable_project(self.root)
        relocated = Path(self._temporary_directory.name) / "Relocated"
        shutil.copytree(self.root, relocated)
        self.assertTrue(ProjectValidator(_NoImport()).validate(relocated, project).is_valid)

    def test_duplicate_build_and_unknown_asset_are_reported(self) -> None:
        project = _persistable_project(self.root)
        bad = ModelicaBuildProvenance(
            _BUILD_ID,
            project.modelica_build_provenance[0].source_snapshot,
            "Example.FirstOrder",
            "OpenModelica",
            "1.26.3",
            "2.0",
            "cs",
            "missing",
        )
        report = ProjectValidator(_NoImport()).validate(
            self.root,
            SimulationProject(
                "p", "Demo", project.model_assets, schema_version="1.1", modelica_build_provenance=(project.modelica_build_provenance[0], bad)
            ),
        )
        codes = {issue.code for issue in report.issues}
        self.assertIn("DUPLICATE_BUILD_ID", codes)
        self.assertIn("UNKNOWN_FMU_ASSET_ID", codes)
        with self.assertRaises(EngineError) as raised:
            self.repository.save(
                self.root,
                SimulationProject(
                    "p",
                    "Demo",
                    project.model_assets,
                    schema_version="1.1",
                    modelica_build_provenance=(project.modelica_build_provenance[0], bad),
                ),
            )
        self.assertIs(ErrorCode.PROJECT_FORMAT_ERROR, raised.exception.code)

    def test_explicit_migration_preserves_project_data_and_writes_backup(self) -> None:
        old = _legacy_project(self.root)
        self.repository.save(self.root, old)
        original = (self.root / "project.json").read_bytes()
        service = ProjectSchemaMigrationService(self.repository, _legacy_validator(self.root))

        result = service.migrate_1_0_to_1_1(self.root)

        self.assertEqual(LATEST_PROJECT_SCHEMA_VERSION, result.project.schema_version)
        self.assertEqual((), result.project.modelica_build_provenance)
        self.assertEqual(old.model_assets, result.project.model_assets)
        self.assertEqual(old.simulation_cases, result.project.simulation_cases)
        self.assertEqual(old.run_history, result.project.run_history)
        self.assertEqual(original, (self.root / result.backup_relative_path).read_bytes())
        self.assertEqual(result.project, self.repository.load(self.root))
        with self.assertRaises(EngineError):
            service.migrate_1_0_to_1_1(self.root)

    def test_migration_replace_failure_preserves_original_project(self) -> None:
        old = _legacy_project(self.root)
        self.repository.save(self.root, old)
        original = (self.root / "project.json").read_bytes()
        service = ProjectSchemaMigrationService(self.repository, _legacy_validator(self.root))
        with patch(
            "farcel.infrastructure.project.json_repository.os.replace",
            side_effect=OSError("blocked"),
        ):
            with self.assertRaises(EngineError) as raised:
                service.migrate_1_0_to_1_1(self.root)
        self.assertIs(ErrorCode.PROJECT_IO_ERROR, raised.exception.code)
        self.assertEqual(original, (self.root / "project.json").read_bytes())

    def test_migration_rejects_concurrent_document_change(self) -> None:
        old = _legacy_project(self.root)
        self.repository.save(self.root, old)
        candidate = self.repository.load_1_0_migration_candidate(self.root)
        self.repository.save(self.root, SimulationProject("changed", "并发修改"))
        with self.assertRaises(EngineError) as raised:
            self.repository.migrate_1_0_to_1_1(self.root, candidate)
        self.assertIs(ErrorCode.PROJECT_IO_ERROR, raised.exception.code)


class _NoImport:
    def load(self, path: Path) -> object:
        raise AssertionError(f"no graph node should import {path}")


def _persistable_project(root: Path) -> SimulationProject:
    source = root / f"modelica-builds/build-{_BUILD_ID}/source/model.mo"
    fmu = root / f"modelica-builds/build-{_BUILD_ID}/model/model.fmu"
    source.parent.mkdir(parents=True, exist_ok=True)
    fmu.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("model FirstOrder end FirstOrder;", encoding="utf-8")
    fmu.write_bytes(b"fmu")
    asset = ModelAsset("modelica-build-" + _BUILD_ID, "一阶系统", str(fmu.relative_to(root)).replace("\\", "/"), _sha256(fmu))
    provenance = ModelicaBuildProvenance(
        _BUILD_ID,
        ModelicaSourceSnapshot(str(source.relative_to(root)).replace("\\", "/"), _sha256(source)),
        "Example.FirstOrder",
        "OpenModelica",
        "1.26.3",
        "2.0",
        "cs",
        asset.asset_id,
    )
    return SimulationProject("p", "含来源项目", (asset,), schema_version="1.1", modelica_build_provenance=(provenance,))


def _provenance() -> ModelicaBuildProvenance:
    return ModelicaBuildProvenance(_BUILD_ID, ModelicaSourceSnapshot("sources/a.mo", "a" * 64), "A", "OMC", "1", "2.0", "cs", "asset")


def _legacy_project(root: Path) -> SimulationProject:
    contents = b"legacy-fmu"
    fmu = root / "models/plant.fmu"
    fmu.parent.mkdir(parents=True, exist_ok=True)
    fmu.write_bytes(contents)
    asset = ModelAsset("plant", "Plant", "models/plant.fmu", _sha256(fmu))
    case = SimulationCase(
        "case",
        "案例",
        SimulationGraph(nodes=(ModelNode("plant", asset.relative_path),)),
        GraphSimulationConfig(),
    )
    record = ProjectRunRecord("run", "case", SimulationState.COMPLETED, 1.0, 1, "results/run.json")
    return SimulationProject("p", "旧项目", (asset,), (case,), (record,))


class _LegacyImporter:
    def __init__(self, root: Path) -> None:
        self._path = str((root / "models/plant.fmu").resolve())

    def load(self, path: Path) -> ModelMetadata:
        if str(path) != self._path:
            raise AssertionError(path)
        return ModelMetadata(
            model_id="plant",
            source_path=self._path,
            fmi_version="2.0",
            model_name="Plant",
            interface_types=(InterfaceType.CO_SIMULATION,),
            executable_interface=InterfaceType.CO_SIMULATION,
            interface_capabilities=(
                InterfaceCapability(InterfaceType.CO_SIMULATION, can_execute=True),
            ),
        )


def _legacy_validator(root: Path) -> ProjectValidator:
    return ProjectValidator(_LegacyImporter(root))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_document(root: Path) -> dict[str, object]:
    return json.loads((root / "project.json").read_text(encoding="utf-8"))


def _write_document(root: Path, document: dict[str, object]) -> None:
    (root / "project.json").write_text(json.dumps(document), encoding="utf-8")
