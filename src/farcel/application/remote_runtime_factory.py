from __future__ import annotations

from pathlib import Path

from farcel.application.remote_node_runtime import RemoteNodeRuntime
from farcel.application.worker_asset_staging import WorkerAssetStager
from farcel.application.worker_client import WorkerRpcClient
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import SimulationConfig
from farcel.contracts.worker_protocol import (
    CreateRuntimeRequest,
    CreateRuntimeResponse,
    WorkerMessageType,
)


class RemoteNodeRuntimeFactory:
    """在既有 Worker connection 上 provision 一个未初始化的远端 runtime。"""

    def __init__(
        self,
        client: WorkerRpcClient,
        asset_stager: WorkerAssetStager | None = None,
    ) -> None:
        self._client = client
        self._asset_stager = asset_stager or WorkerAssetStager(client)

    def create(
        self,
        node_id: str,
        model_path: str | Path,
        config: SimulationConfig,
    ) -> RemoteNodeRuntime:
        if not isinstance(node_id, str) or not node_id.strip():
            raise EngineError(
                ErrorCode.VALIDATION_ERROR,
                "node_id 必须是非空字符串",
                {"phase": "remote_runtime_creation"},
            )
        if not isinstance(config, SimulationConfig):
            raise EngineError(
                ErrorCode.VALIDATION_ERROR,
                "config 必须是 SimulationConfig",
                {"phase": "remote_runtime_creation"},
            )
        asset_sha256 = self._asset_stager.ensure_asset(model_path)
        response = self._client.request(
            WorkerMessageType.CREATE_RUNTIME,
            CreateRuntimeRequest(node_id, asset_sha256, config),
        )
        if (
            not isinstance(response, CreateRuntimeResponse)
            or not isinstance(response.runtime_id, str)
            or not response.runtime_id.strip()
        ):
            raise EngineError(
                ErrorCode.INTERNAL_ERROR,
                "Worker runtime creation response 无效",
                {
                    "phase": "remote_runtime_creation",
                    "worker_id": self._client.worker_id,
                    "node_id": node_id,
                    "asset_sha256": asset_sha256,
                    "issue_code": "UNEXPECTED_WORKER_PAYLOAD",
                },
            )
        return RemoteNodeRuntime(self._client, response.runtime_id)
