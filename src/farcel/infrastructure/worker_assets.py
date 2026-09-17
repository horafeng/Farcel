"""Worker 自有的本地 content-addressed FMU asset cache。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
from uuid import uuid4

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.infrastructure.worker_protocol.framing import MAX_ASSET_FRAME_BYTES


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_HASH_CHUNK_SIZE = 64 * 1024


class LocalWorkerAssetCache:
    """以 canonical SHA-256 作为唯一 identity 的 Worker 本地 FMU cache。"""

    def __init__(self, cache_root: Path) -> None:
        self._cache_root = Path(cache_root)

    def has_asset(self, sha256: str) -> bool:
        self._validate_sha256(sha256)
        try:
            path = self._asset_path(sha256, create_root=False)
            return self._is_verified_file(path, sha256)
        except EngineError:
            raise
        except OSError as exc:
            raise _io_error("worker_asset_has", sha256, "无法检查 Worker asset cache", exc) from None

    def put_asset(self, sha256: str, content: bytes) -> None:
        self._validate_sha256(sha256)
        self._validate_content(sha256, content)

        temporary_path: Path | None = None
        try:
            final_path = self._asset_path(sha256, create_root=True)
            if self._is_verified_file(final_path, sha256, replace_untrusted=True):
                return

            temporary_path = final_path.with_name(f".{sha256}.{uuid4().hex}.tmp")
            with temporary_path.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, final_path)
        except EngineError:
            raise
        except OSError as exc:
            raise _io_error("worker_asset_put", sha256, "无法写入 Worker asset cache", exc) from None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def resolve_asset(self, sha256: str) -> Path:
        self._validate_sha256(sha256)
        try:
            path = self._asset_path(sha256, create_root=False)
            if self._is_verified_file(path, sha256):
                return path
        except EngineError:
            raise
        except OSError as exc:
            raise _io_error("worker_asset_resolve", sha256, "无法解析 Worker asset cache", exc) from None

        raise EngineError(
            ErrorCode.IMPORT_ERROR,
            "Worker asset cache 中不存在已验证的 asset",
            {
                "phase": "worker_asset_resolve",
                "sha256": sha256,
                "path": str(path),
            },
        )

    def _asset_path(self, sha256: str, *, create_root: bool) -> Path:
        assets_root = self._assets_root(create=create_root)
        path = assets_root / f"{sha256}.fmu"
        root_real = assets_root.resolve(strict=False)
        if path.parent.resolve(strict=False) != root_real:
            raise OSError("asset cache path 离开了 assets root")
        return path

    def _assets_root(self, *, create: bool) -> Path:
        root = self._cache_root.resolve(strict=False)
        assets_root = root / "assets"
        if assets_root.is_symlink():
            raise OSError("assets root 不能是 symlink")
        if assets_root.exists():
            if not assets_root.is_dir():
                raise OSError("assets root 必须是目录")
        elif create:
            assets_root.mkdir(parents=True, exist_ok=True)
            if assets_root.is_symlink() or not assets_root.is_dir():
                raise OSError("assets root 必须是非 symlink 目录")
        return assets_root

    @staticmethod
    def _is_verified_file(
        path: Path,
        sha256: str,
        *,
        replace_untrusted: bool = False,
    ) -> bool:
        if path.is_symlink() or not path.exists():
            return False
        if not path.is_file():
            if replace_untrusted and path.is_dir():
                path.rmdir()
                return False
            raise OSError("asset cache entry 必须是普通文件")
        return _sha256_file(path) == sha256

    @staticmethod
    def _validate_sha256(sha256: object) -> None:
        if not isinstance(sha256, str) or _SHA256_PATTERN.fullmatch(sha256) is None:
            raise EngineError(
                ErrorCode.VALIDATION_ERROR,
                "sha256 必须是 64 位小写十六进制",
                {"phase": "worker_asset_validate"},
            )

    @staticmethod
    def _validate_content(sha256: str, content: object) -> None:
        if not isinstance(content, bytes):
            raise EngineError(
                ErrorCode.VALIDATION_ERROR,
                "asset content 必须是 bytes",
                {"phase": "worker_asset_put", "sha256": sha256},
            )
        if len(content) > MAX_ASSET_FRAME_BYTES:
            raise EngineError(
                ErrorCode.VALIDATION_ERROR,
                "asset content 超过允许大小",
                {"phase": "worker_asset_put", "sha256": sha256},
            )
        if hashlib.sha256(content).hexdigest() != sha256:
            raise EngineError(
                ErrorCode.VALIDATION_ERROR,
                "asset content SHA-256 不匹配",
                {"phase": "worker_asset_put", "sha256": sha256},
            )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _io_error(phase: str, sha256: str, message: str, cause: OSError) -> EngineError:
    return EngineError(
        ErrorCode.PROJECT_IO_ERROR,
        message,
        {"phase": phase, "sha256": sha256, "diagnostic": str(cause)},
    )
