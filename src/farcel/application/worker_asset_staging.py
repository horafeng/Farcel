from __future__ import annotations

import hashlib
from pathlib import Path

from farcel.application.worker_client import WorkerRpcClient
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.worker_protocol import (
    HasAssetRequest,
    HasAssetResponse,
    PutAssetRequest,
    PutAssetResponse,
    WorkerMessageType,
)


_HASH_CHUNK_BYTES = 64 * 1024


class WorkerAssetStager:
    """将 Coordinator 本地 asset 安全准备为 Worker SHA-256 cache entry。"""

    def __init__(self, client: WorkerRpcClient) -> None:
        self._client = client

    def ensure_asset(self, model_path: str | Path) -> str:
        path = self._validate_source_path(model_path)
        expected_sha256, expected_size = self._hash_source(path)
        has_response = self._client.request(
            WorkerMessageType.HAS_ASSET,
            HasAssetRequest(expected_sha256),
        )
        if (
            not isinstance(has_response, HasAssetResponse)
            or has_response.sha256 != expected_sha256
            or type(has_response.available) is not bool
        ):
            raise self._unexpected_payload_error(expected_sha256)
        if has_response.available:
            return expected_sha256

        content = self._read_source(path)
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if len(content) != expected_size or actual_sha256 != expected_sha256:
            raise EngineError(
                ErrorCode.IMPORT_ERROR,
                "FMU 在远端 staging 期间发生变化",
                {
                    "phase": "remote_asset_staging",
                    "issue_code": "REMOTE_ASSET_CHANGED_DURING_STAGING",
                    "model_path": str(path),
                    "expected_sha256": expected_sha256,
                    "actual_sha256": actual_sha256,
                    "expected_size": expected_size,
                    "actual_size": len(content),
                },
            )
        put_response = self._client.request(
            WorkerMessageType.PUT_ASSET,
            PutAssetRequest(expected_sha256, len(content)),
            binary_payload=content,
        )
        if not isinstance(put_response, PutAssetResponse) or put_response.sha256 != expected_sha256:
            raise self._unexpected_payload_error(expected_sha256)
        return expected_sha256

    @staticmethod
    def _validate_source_path(model_path: str | Path) -> Path:
        try:
            path = Path(model_path)
            if not path.exists() or not path.is_file():
                raise ValueError("source 不存在或不是普通文件")
        except (OSError, TypeError, ValueError) as exc:
            raise EngineError(
                ErrorCode.IMPORT_ERROR,
                "Worker staging source 无效",
                {
                    "phase": "remote_asset_staging",
                    "issue_code": "REMOTE_ASSET_SOURCE_INVALID",
                    "model_path": str(model_path),
                    "diagnostic": str(exc),
                },
            ) from None
        return path

    @staticmethod
    def _hash_source(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        try:
            with path.open("rb") as source:
                while chunk := source.read(_HASH_CHUNK_BYTES):
                    digest.update(chunk)
                    size += len(chunk)
        except OSError as exc:
            raise EngineError(
                ErrorCode.IMPORT_ERROR,
                "无法读取 Worker staging source",
                {
                    "phase": "remote_asset_staging",
                    "issue_code": "REMOTE_ASSET_READ_FAILED",
                    "model_path": str(path),
                    "diagnostic": str(exc),
                },
            ) from None
        return digest.hexdigest(), size

    @staticmethod
    def _read_source(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise EngineError(
                ErrorCode.IMPORT_ERROR,
                "无法读取 Worker staging source",
                {
                    "phase": "remote_asset_staging",
                    "issue_code": "REMOTE_ASSET_READ_FAILED",
                    "model_path": str(path),
                    "diagnostic": str(exc),
                },
            ) from None

    def _unexpected_payload_error(self, sha256: str) -> EngineError:
        return EngineError(
            ErrorCode.INTERNAL_ERROR,
            "Worker asset response 无效",
            {
                "phase": "remote_asset_staging",
                "issue_code": "UNEXPECTED_WORKER_PAYLOAD",
                "worker_id": self._client.worker_id,
                "sha256": sha256,
            },
        )
