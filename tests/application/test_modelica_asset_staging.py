from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from farcel.application.modelica_asset_staging import ModelicaAssetStagingService
from farcel.contracts import (
    EngineError,
    ErrorCode,
    ModelicaAssetStagingRequest,
    ModelicaCompileRequest,
    ModelicaCompileResult,
)
from farcel.infrastructure.project import LocalModelicaAssetStore


class _Compiler:
    compiler_name = "TestModelicaCompiler"

    def __init__(self, *, fail: EngineError | None = None, mismatch_hash: bool = False) -> None:
        self._fail = fail
        self._mismatch_hash = mismatch_hash
        self.requests: list[ModelicaCompileRequest] = []
        self.source_contents: list[str] = []
        self.after_compile = None

    def compile(self, request: ModelicaCompileRequest) -> ModelicaCompileResult:
        self.requests.append(request)
        if self._fail is not None:
            raise self._fail
        output = Path(request.output_directory) / "Generated.fmu"
        self.source_contents.append(Path(request.source_path).read_text(encoding="utf-8"))
        output.write_bytes(b"fake-fmu")
        if self.after_compile is not None:
            self.after_compile()
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        return ModelicaCompileResult(
            fmu_path=output,
            fmi_version="2.0",
            fmu_type="cs",
            compiler_version="test-1.0",
            sha256=("a" * 64 if self._mismatch_hash else digest),
        )


class ModelicaAssetStagingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary_directory.name) / "Project"
        self.root.mkdir()
        self.project_json = self.root / "project.json"
        self.project_json.write_bytes(b'{"schema_version":"1.0"}\n')
        self.source = Path(self._temporary_directory.name) / "FirstOrder.mo"
        self.source.write_text("model FirstOrder end FirstOrder;", encoding="utf-8")

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_uses_saved_snapshot_for_compilation_and_returns_unregistered_assets(self) -> None:
        compiler = _Compiler()
        original_project_json = self.project_json.read_bytes()
        staged = self._service(compiler).stage(self._request())

        self.assertEqual(1, len(compiler.requests))
        compile_request = compiler.requests[0]
        self.assertNotEqual(self.source, Path(compile_request.source_path))
        self.assertIn(".farcel-modelica-staging", str(compile_request.source_path))
        self.assertEqual(self.source.read_text(encoding="utf-8"), compiler.source_contents[0])
        self.assertEqual(staged.model_asset.asset_id, staged.provenance.fmu_asset_id)
        self.assertEqual("TestModelicaCompiler", staged.provenance.compiler_name)
        self.assertTrue((self.root / staged.model_asset.relative_path).is_file())
        self.assertEqual(original_project_json, self.project_json.read_bytes())

    def test_source_change_after_snapshot_does_not_change_the_saved_provenance(self) -> None:
        compiler = _Compiler()
        compiler.after_compile = lambda: self.source.write_text(
            "model Changed end Changed;", encoding="utf-8"
        )

        staged = self._service(compiler).stage(self._request())

        saved_source = self.root / staged.provenance.source_snapshot.relative_path
        self.assertNotEqual(self.source.read_bytes(), saved_source.read_bytes())
        self.assertEqual(
            hashlib.sha256(saved_source.read_bytes()).hexdigest(),
            staged.provenance.source_snapshot.sha256,
        )

    def test_compiler_failure_or_bad_fmu_never_publishes_a_completed_build(self) -> None:
        failed = _Compiler(fail=EngineError(ErrorCode.MODELICA_COMPILATION_ERROR, "失败"))
        with self.assertRaises(EngineError) as failure:
            self._service(failed).stage(self._request())
        self.assertEqual(ErrorCode.MODELICA_COMPILATION_ERROR, failure.exception.code)
        self.assertFalse((self.root / "modelica-builds").exists())

        mismatch = _Compiler(mismatch_hash=True)
        with self.assertRaises(EngineError) as hash_failure:
            self._service(mismatch).stage(self._request())
        self.assertEqual(ErrorCode.MODELICA_STAGING_ERROR, hash_failure.exception.code)
        self.assertFalse((self.root / "modelica-builds").exists())

    def test_existing_build_id_conflict_preserves_existing_files(self) -> None:
        build_id = "b" * 32
        existing = self.root / "modelica-builds" / f"build-{build_id}"
        existing.mkdir(parents=True)
        sentinel = existing / "sentinel"
        sentinel.write_text("keep", encoding="utf-8")
        with patch(
            "farcel.application.modelica_asset_staging.uuid.uuid4",
            return_value=SimpleNamespace(hex=build_id),
        ):
            with self.assertRaises(EngineError) as raised:
                self._service(_Compiler()).stage(self._request())

        self.assertEqual(ErrorCode.MODELICA_OUTPUT_CONFLICT, raised.exception.code)
        self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))

    def test_invalid_staging_request_is_rejected_before_compiler_call(self) -> None:
        compiler = _Compiler()
        with self.assertRaises(EngineError) as raised:
            self._service(compiler).stage(replace(self._request(), model_name="bad; name"))

        self.assertEqual(ErrorCode.CONFIG_ERROR, raised.exception.code)
        self.assertEqual([], compiler.requests)

    def test_multiple_operations_use_distinct_build_identifiers(self) -> None:
        first = self._service(_Compiler()).stage(self._request())
        second = self._service(_Compiler()).stage(self._request())

        self.assertNotEqual(first.provenance.build_id, second.provenance.build_id)
        self.assertNotEqual(first.model_asset.relative_path, second.model_asset.relative_path)

    def _service(self, compiler: _Compiler) -> ModelicaAssetStagingService:
        return ModelicaAssetStagingService(compiler, LocalModelicaAssetStore())

    def _request(self) -> ModelicaAssetStagingRequest:
        return ModelicaAssetStagingRequest(
            project_root=self.root,
            source_path=self.source,
            model_name="FirstOrder",
        )
