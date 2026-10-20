"""Filesystem implementation of unpublished Modelica build-package staging."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.modelica_provenance import (
    ModelicaSourceSnapshot,
    ModelicaStagingWorkspace,
)
from farcel.contracts.project_paths import is_lowercase_sha256


_HASH_CHUNK_SIZE = 1024 * 1024
_BUILD_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_STAGING_DIRECTORY = ".farcel-modelica-staging"
_BUILD_DIRECTORY = "modelica-builds"
_MARKER_NAME = ".farcel-modelica-staging"
_SOURCE_RELATIVE_NAME = "source/model.mo"
_FMU_RELATIVE_NAME = "model/model.fmu"


class LocalModelicaAssetStore:
    """Create, verify and publish one self-contained local Modelica build.

    The final directory is renamed from a store-owned sibling workspace after
    both source and FMU hashes have been checked.  No Project DTO or JSON file
    is read or written here.
    """

    def prepare_source_snapshot(
        self,
        project_root: Path,
        source_path: Path,
        build_id: str,
    ) -> ModelicaStagingWorkspace:
        root = _resolve_project_root(project_root)
        _require_build_id(build_id)
        source = _validate_source_file(source_path)
        staging_root = _controlled_directory(root, _STAGING_DIRECTORY)
        workspace = staging_root / _build_directory_name(build_id)
        try:
            workspace.mkdir()
        except FileExistsError:
            raise _conflict("暂存目录已存在，拒绝覆盖。", build_id) from None
        except OSError:
            raise _staging_error("无法创建 Modelica 暂存目录。", "workspace_create") from None

        try:
            _write_marker(workspace, build_id)
            snapshot_path = workspace / _SOURCE_RELATIVE_NAME
            snapshot_path.parent.mkdir()
            source_hash = _copy_consistent_source(source, snapshot_path)
            compiler_output = workspace / "compiler-output"
            compiler_output.mkdir()
            source_snapshot = ModelicaSourceSnapshot(
                _project_relative(build_id, _SOURCE_RELATIVE_NAME),
                source_hash,
            )
            return ModelicaStagingWorkspace(
                build_id=build_id,
                project_root=root,
                workspace_path=workspace,
                source_path=snapshot_path,
                compiler_output_directory=compiler_output,
                source_snapshot=source_snapshot,
                fmu_relative_path=_project_relative(build_id, _FMU_RELATIVE_NAME),
            )
        except Exception:
            _discard_owned_directory(root, workspace, build_id)
            raise

    def verify_source_snapshot(self, workspace: ModelicaStagingWorkspace) -> None:
        _verify_workspace(workspace)
        try:
            actual = _sha256(workspace.source_path)
        except OSError:
            raise _staging_error("无法读取已暂存的 Modelica 源快照。", "snapshot_verify") from None
        if actual != workspace.source_snapshot.sha256:
            raise _staging_error("Modelica 源快照 SHA-256 校验失败。", "snapshot_verify")

    def publish_verified_build(
        self,
        workspace: ModelicaStagingWorkspace,
        fmu_path: Path,
        fmu_sha256: str,
    ) -> Path:
        _verify_workspace(workspace)
        self.verify_source_snapshot(workspace)
        if not is_lowercase_sha256(fmu_sha256):
            raise _staging_error("FMU SHA-256 格式无效。", "fmu_verify")
        fmu = _validate_workspace_fmu(workspace, fmu_path)
        try:
            if _sha256(fmu) != fmu_sha256:
                raise _staging_error("FMU SHA-256 校验失败。", "fmu_verify")
            packaged_fmu = workspace.workspace_path / _FMU_RELATIVE_NAME
            packaged_fmu.parent.mkdir()
            _copy_file_exclusive(fmu, packaged_fmu)
            if _sha256(packaged_fmu) != fmu_sha256:
                raise _staging_error("已暂存 FMU SHA-256 校验失败。", "fmu_publish")
            self.verify_source_snapshot(workspace)
            final_parent = _controlled_directory(workspace.project_root, _BUILD_DIRECTORY)
            final_directory = final_parent / _build_directory_name(workspace.build_id)
            if final_directory.exists() or final_directory.is_symlink():
                raise _conflict("正式构建目录已存在，拒绝覆盖。", workspace.build_id)
            try:
                os.rename(workspace.workspace_path, final_directory)
            except FileExistsError:
                raise _conflict("正式构建目录已存在，拒绝覆盖。", workspace.build_id) from None
            except OSError:
                raise _staging_error("无法发布已验证的 Modelica 构建目录。", "publish") from None
            return final_directory / _FMU_RELATIVE_NAME
        except EngineError:
            raise
        except OSError:
            raise _staging_error("无法暂存已验证的 FMU。", "fmu_publish") from None

    def discard_workspace(self, workspace: ModelicaStagingWorkspace) -> None:
        _discard_owned_directory(
            workspace.project_root,
            workspace.workspace_path,
            workspace.build_id,
        )

    def discard_published_build(self, project_root: Path, build_id: str) -> None:
        root = _resolve_project_root(project_root)
        _require_build_id(build_id)
        _discard_owned_directory(
            root,
            root / _BUILD_DIRECTORY / _build_directory_name(build_id),
            build_id,
        )


def _resolve_project_root(project_root: Path) -> Path:
    try:
        root = Path(project_root)
        if not root.exists() or not root.is_dir():
            raise _staging_error("project_root 必须是已存在的目录。", "project_root")
        return root.resolve(strict=True)
    except EngineError:
        raise
    except (OSError, RuntimeError):
        raise _staging_error("无法安全解析 project_root。", "project_root") from None


def _validate_source_file(source_path: Path) -> Path:
    try:
        source = Path(source_path)
        if source.is_symlink():
            raise _staging_error("当前不支持符号链接形式的 Modelica 源文件。", "source_input")
        if not source.exists() or not source.is_file():
            raise _staging_error("Modelica 源文件不存在或不是普通文件。", "source_input")
        if source.suffix.lower() != ".mo":
            raise _staging_error("当前仅支持 .mo 源文件快照。", "source_input")
        return source.resolve(strict=True)
    except EngineError:
        raise
    except (OSError, RuntimeError):
        raise _staging_error("无法安全访问 Modelica 源文件。", "source_input") from None


def _controlled_directory(project_root: Path, name: str) -> Path:
    path = project_root / name
    try:
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_dir():
                raise _staging_error("Modelica 构建目录不能是符号链接或普通文件。", "directory")
        else:
            path.mkdir()
        resolved = path.resolve(strict=True)
    except EngineError:
        raise
    except (OSError, RuntimeError):
        raise _staging_error("无法创建或解析 Modelica 构建目录。", "directory") from None
    if not _is_within(project_root, resolved):
        raise _staging_error("Modelica 构建目录解析后不能离开 project_root。", "directory")
    return resolved


def _copy_consistent_source(source: Path, destination: Path) -> str:
    try:
        before = _sha256(source)
        _copy_file_exclusive(source, destination)
        snapshot_hash = _sha256(destination)
        after = _sha256(source)
    except OSError:
        raise _staging_error("无法创建 Modelica 源快照。", "snapshot_copy") from None
    if before != after or snapshot_hash != before:
        raise _staging_error("Modelica 源文件在快照复制期间发生变化。", "snapshot_copy")
    return snapshot_hash


def _validate_workspace_fmu(workspace: ModelicaStagingWorkspace, fmu_path: Path) -> Path:
    try:
        fmu = Path(fmu_path)
        if fmu.is_symlink() or not fmu.exists() or not fmu.is_file():
            raise _staging_error("编译器未返回普通 FMU 文件。", "fmu_verify")
        resolved = fmu.resolve(strict=True)
    except EngineError:
        raise
    except (OSError, RuntimeError):
        raise _staging_error("无法安全访问编译后的 FMU。", "fmu_verify") from None
    output_root = workspace.compiler_output_directory.resolve()
    if not _is_within(output_root, resolved):
        raise _staging_error("编译后的 FMU 必须位于当前暂存工作目录。", "fmu_verify")
    return resolved


def _verify_workspace(workspace: ModelicaStagingWorkspace) -> None:
    _require_build_id(workspace.build_id)
    marker = workspace.workspace_path / _MARKER_NAME
    try:
        if workspace.workspace_path.is_symlink() or marker.read_text(encoding="utf-8") != workspace.build_id:
            raise _staging_error("Modelica 暂存工作目录不属于当前构建。", "workspace_verify")
        if not _is_within(workspace.project_root, workspace.workspace_path.resolve(strict=True)):
            raise _staging_error("Modelica 暂存工作目录解析后不能离开 project_root。", "workspace_verify")
    except EngineError:
        raise
    except (OSError, RuntimeError):
        raise _staging_error("无法验证 Modelica 暂存工作目录。", "workspace_verify") from None


def _discard_owned_directory(project_root: Path, directory: Path, build_id: str) -> None:
    marker = directory / _MARKER_NAME
    try:
        if not directory.exists() and not directory.is_symlink():
            return
        if directory.is_symlink():
            return
        resolved = directory.resolve(strict=True)
        if not _is_within(project_root, resolved):
            return
        if (resolved / _MARKER_NAME).read_text(encoding="utf-8") != build_id:
            return
        shutil.rmtree(resolved)
        parent = resolved.parent
        if parent.name == _STAGING_DIRECTORY:
            parent.rmdir()
    except OSError:
        raise _staging_error("无法清理当前 Modelica 暂存资源。", "cleanup") from None


def _copy_file_exclusive(source: Path, destination: Path) -> None:
    with source.open("rb") as input_file, destination.open("xb") as output_file:
        shutil.copyfileobj(input_file, output_file, _HASH_CHUNK_SIZE)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        while chunk := artifact.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _build_directory_name(build_id: str) -> str:
    return f"build-{build_id}"


def _project_relative(build_id: str, child: str) -> str:
    return f"{_BUILD_DIRECTORY}/{_build_directory_name(build_id)}/{child}"


def _write_marker(workspace: Path, build_id: str) -> None:
    (workspace / _MARKER_NAME).write_text(build_id, encoding="utf-8")


def _require_build_id(build_id: str) -> None:
    if not isinstance(build_id, str) or _BUILD_ID_PATTERN.fullmatch(build_id) is None:
        raise _staging_error("build_id 必须是 32 位小写十六进制标识。", "build_id")


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _conflict(message: str, build_id: str) -> EngineError:
    return EngineError(
        ErrorCode.MODELICA_OUTPUT_CONFLICT,
        message,
        {"stage": "publish", "build_id": build_id},
    )


def _staging_error(message: str, stage: str) -> EngineError:
    return EngineError(ErrorCode.MODELICA_STAGING_ERROR, message, {"stage": stage})
