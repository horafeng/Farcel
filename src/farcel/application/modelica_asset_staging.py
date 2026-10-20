"""Application orchestration for unregistered Modelica build-package staging."""

from __future__ import annotations

from pathlib import Path
import uuid

from farcel.application.modelica_provenance_validation import (
    require_valid_modelica_build_provenance,
)
from farcel.application.modelica_validation import require_valid_modelica_compile_request
from farcel.application.project_assets import ProjectAssetValidator
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.modelica import ModelicaCompileRequest, ModelicaCompileResult
from farcel.contracts.modelica_provenance import (
    ModelicaAssetStagingRequest,
    ModelicaBuildProvenance,
    ModelicaStagedBuild,
    ModelicaStagingWorkspace,
)
from farcel.contracts.ports import ModelicaAssetStore, ModelicaCompiler
from farcel.contracts.project import ModelAsset
from farcel.contracts.project_paths import is_lowercase_sha256


class ModelicaAssetStagingService:
    """Stage a verified build package without changing a SimulationProject."""

    def __init__(
        self,
        compiler: ModelicaCompiler,
        asset_store: ModelicaAssetStore,
        asset_validator: ProjectAssetValidator | None = None,
    ) -> None:
        self._compiler = compiler
        self._asset_store = asset_store
        self._asset_validator = asset_validator or ProjectAssetValidator()

    def stage(self, request: ModelicaAssetStagingRequest) -> ModelicaStagedBuild:
        """Snapshot, compile, verify and publish one unregistered build package."""

        try:
            project_root = Path(request.project_root)
        except (TypeError, ValueError):
            raise EngineError(
                ErrorCode.CONFIG_ERROR,
                "project_root 必须是可解析的目录路径。",
                {"field": "project_root"},
            ) from None
        _require_pre_staging_request(request, project_root)
        workspace: ModelicaStagingWorkspace | None = None
        try:
            build_id = uuid.uuid4().hex
            workspace = self._asset_store.prepare_source_snapshot(
                project_root,
                Path(request.source_path),
                build_id,
            )
            self._asset_store.verify_source_snapshot(workspace)
            compile_request = ModelicaCompileRequest(
                source_path=workspace.source_path,
                model_name=request.model_name,
                output_directory=workspace.compiler_output_directory,
                compiler_path=request.compiler_path,
                fmi_version=request.fmi_version,
                fmu_type=request.fmu_type,
                timeout_seconds=request.timeout_seconds,
            )
            require_valid_modelica_compile_request(compile_request)
            compiled = self._compiler.compile(compile_request)
            self._asset_store.verify_source_snapshot(workspace)
            _require_compiled_result_matches_request(compiled, request)
            model_asset = ModelAsset(
                asset_id=f"modelica-build-{build_id}",
                display_name=request.model_name,
                relative_path=workspace.fmu_relative_path,
                sha256=compiled.sha256,
            )
            provenance = ModelicaBuildProvenance(
                build_id=build_id,
                source_snapshot=workspace.source_snapshot,
                model_name=request.model_name,
                compiler_name=self._compiler.compiler_name,
                compiler_version=compiled.compiler_version,
                fmi_version=compiled.fmi_version,
                fmu_type=compiled.fmu_type,
                fmu_asset_id=model_asset.asset_id,
            )
            require_valid_modelica_build_provenance(
                provenance,
                model_assets=(model_asset,),
            )
            published_fmu = self._asset_store.publish_verified_build(
                workspace,
                Path(compiled.fmu_path),
                compiled.sha256,
            )
            check = self._asset_validator.check(workspace.project_root, model_asset)
            if not _matches_published_asset(check.resolved_path, published_fmu) or not check.report.is_valid:
                self._discard_published(workspace)
                raise EngineError(
                    ErrorCode.MODELICA_STAGING_ERROR,
                    "已发布的 Modelica FMU 资产验证失败。",
                    {
                        "stage": "asset_validation",
                        "issues": tuple(
                            {
                                "field": issue.field,
                                "code": issue.code,
                                "message": issue.message,
                            }
                            for issue in check.report.issues
                        ),
                    },
                )
            return ModelicaStagedBuild(model_asset, provenance)
        except EngineError:
            if workspace is not None:
                self._discard_workspace_after_failure(workspace)
            raise

    def _discard_workspace_after_failure(self, workspace: ModelicaStagingWorkspace) -> None:
        try:
            self._asset_store.discard_workspace(workspace)
        except EngineError:
            # The ownership marker leaves a future, explicit recovery target;
            # never delete an unknown directory while reporting the original error.
            pass

    def _discard_published(self, workspace: ModelicaStagingWorkspace) -> None:
        try:
            self._asset_store.discard_published_build(
                workspace.project_root,
                workspace.build_id,
            )
        except EngineError:
            pass


def _require_pre_staging_request(
    request: ModelicaAssetStagingRequest,
    project_root: Path,
) -> None:
    """Reuse compilation validation before filesystem staging begins."""

    require_valid_modelica_compile_request(
        ModelicaCompileRequest(
            source_path=request.source_path,
            model_name=request.model_name,
            output_directory=project_root,
            compiler_path=request.compiler_path,
            fmi_version=request.fmi_version,
            fmu_type=request.fmu_type,
            timeout_seconds=request.timeout_seconds,
        )
    )


def _require_compiled_result_matches_request(
    compiled: ModelicaCompileResult,
    request: ModelicaAssetStagingRequest,
) -> None:
    if (
        compiled.fmi_version != request.fmi_version
        or compiled.fmu_type != request.fmu_type
        or not isinstance(compiled.compiler_version, str)
        or not compiled.compiler_version.strip()
        or not is_lowercase_sha256(compiled.sha256)
    ):
        raise EngineError(
            ErrorCode.MODELICA_STAGING_ERROR,
            "Modelica 编译结果与暂存请求不一致或不完整。",
            {"stage": "compile_result"},
        )


def _matches_published_asset(resolved_path: Path | None, published_fmu: Path) -> bool:
    if resolved_path is None:
        return False
    try:
        return resolved_path.samefile(published_fmu)
    except OSError:
        return False
