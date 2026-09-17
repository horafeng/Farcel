from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.ports import WorkerTransportClient
from farcel.contracts.worker_protocol import (
    WorkerMessageType,
    WorkerRequest,
    WorkerResponse,
    engine_error_from_remote_error,
)


class WorkerRpcClient:
    """Coordinator 侧的传输无关 Worker 协议语义 client。"""

    def __init__(
        self,
        worker_id: str,
        transport: WorkerTransportClient,
        *,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise EngineError(
                ErrorCode.VALIDATION_ERROR,
                "worker_id 必须是非空字符串",
                {"phase": "worker_rpc_client"},
            )
        self._worker_id = worker_id
        self._transport = transport
        self._request_id_factory = request_id_factory or (lambda: uuid4().hex)

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def connect(self) -> None:
        self._transport.connect()

    def request(
        self,
        message_type: WorkerMessageType,
        payload: object | None = None,
        *,
        binary_payload: bytes | None = None,
    ) -> object | None:
        request_id = self._new_request_id()
        response = self._transport.request(
            WorkerRequest(
                request_id,
                self._worker_id,
                message_type,
                payload,
            ),
            binary_payload=binary_payload,
        )
        if not isinstance(response, WorkerResponse):
            raise EngineError(
                ErrorCode.INTERNAL_ERROR,
                "Worker 返回的响应类型无效",
                {
                    "phase": "worker_rpc_response",
                    "worker_id": self._worker_id,
                    "message_type": _message_type_value(message_type),
                    "issue_code": "UNEXPECTED_WORKER_RESPONSE",
                },
            )
        if response.ok:
            return response.payload
        if response.error is None:
            raise EngineError(
                ErrorCode.INTERNAL_ERROR,
                "Worker 返回无 error 的失败响应",
                {
                    "phase": "worker_rpc_response",
                    "worker_id": self._worker_id,
                    "message_type": _message_type_value(message_type),
                    "issue_code": "MISSING_REMOTE_ERROR",
                },
            )
        raise engine_error_from_remote_error(response.error)

    def close(self) -> None:
        self._transport.close()

    def _new_request_id(self) -> str:
        request_id = self._request_id_factory()
        if not isinstance(request_id, str) or not request_id.strip():
            raise EngineError(
                ErrorCode.INTERNAL_ERROR,
                "Worker request_id factory 返回无效值",
                {"phase": "worker_rpc_request_id"},
            )
        return request_id


def _message_type_value(message_type: object) -> object:
    return message_type.value if isinstance(message_type, WorkerMessageType) else message_type
