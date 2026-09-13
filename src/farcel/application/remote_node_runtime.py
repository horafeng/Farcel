from __future__ import annotations

import math
from collections.abc import Mapping
from enum import Enum
from typing import Any

from farcel.application.worker_client import WorkerRpcClient
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.worker_protocol import (
    AdvanceToRequest,
    ReadOutputsResponse,
    RuntimeAck,
    RuntimeCommand,
    SetInputsRequest,
    WorkerMessageType,
)


class _RemoteRuntimeState(str, Enum):
    CREATED = "created"
    INITIALIZED = "initialized"
    FAILED = "failed"
    TERMINATED = "terminated"
    CLOSED = "closed"


class RemoteNodeRuntime:
    """将既有 Worker runtime 代理为 `ModelNodeRuntime` lifecycle。"""

    def __init__(self, client: WorkerRpcClient, runtime_id: str) -> None:
        if not isinstance(runtime_id, str) or not runtime_id.strip():
            raise EngineError(
                ErrorCode.VALIDATION_ERROR,
                "runtime_id 必须是非空字符串",
                {"phase": "remote_runtime"},
            )
        self._client = client
        self._runtime_id = runtime_id
        self._state = _RemoteRuntimeState.CREATED

    def initialize(self) -> None:
        if self._state is _RemoteRuntimeState.INITIALIZED:
            return
        if self._state is not _RemoteRuntimeState.CREATED:
            raise self._lifecycle_error(ErrorCode.INITIALIZATION_ERROR, "initialize")
        try:
            payload = self._client.request(
                WorkerMessageType.INITIALIZE,
                RuntimeCommand(self._runtime_id),
            )
            self._require_ack(payload, "initialize")
        except EngineError:
            self._state = _RemoteRuntimeState.FAILED
            raise
        self._state = _RemoteRuntimeState.INITIALIZED

    def set_inputs(self, values: Mapping[str, Any]) -> None:
        self._ensure_initialized(ErrorCode.INPUT_SET_ERROR, "set_inputs")
        if not values:
            return
        try:
            payload = self._client.request(
                WorkerMessageType.SET_INPUTS,
                SetInputsRequest(self._runtime_id, values),
            )
            self._require_ack(payload, "set_inputs")
        except EngineError:
            self._state = _RemoteRuntimeState.FAILED
            raise

    def advance_to(self, target_time: float) -> None:
        self._ensure_initialized(ErrorCode.STEP_ERROR, "advance_to")
        if (
            not isinstance(target_time, (int, float))
            or isinstance(target_time, bool)
            or not math.isfinite(target_time)
        ):
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "target_time 必须是有限数值",
                {"phase": "remote_runtime", "runtime_id": self._runtime_id},
            )
        try:
            payload = self._client.request(
                WorkerMessageType.ADVANCE_TO,
                AdvanceToRequest(self._runtime_id, target_time),
            )
            self._require_ack(payload, "advance_to")
        except EngineError:
            self._state = _RemoteRuntimeState.FAILED
            raise

    def read_outputs(self) -> Mapping[str, Any]:
        self._ensure_initialized(ErrorCode.OUTPUT_READ_ERROR, "read_outputs")
        try:
            payload = self._client.request(
                WorkerMessageType.READ_OUTPUTS,
                RuntimeCommand(self._runtime_id),
            )
            if (
                not isinstance(payload, ReadOutputsResponse)
                or payload.runtime_id != self._runtime_id
                or not isinstance(payload.outputs, Mapping)
            ):
                raise self._unexpected_payload_error("read_outputs")
            return payload.outputs
        except EngineError:
            self._state = _RemoteRuntimeState.FAILED
            raise

    def terminate(self) -> None:
        if self._state in {
            _RemoteRuntimeState.CREATED,
            _RemoteRuntimeState.TERMINATED,
            _RemoteRuntimeState.CLOSED,
        }:
            return
        try:
            payload = self._client.request(
                WorkerMessageType.TERMINATE,
                RuntimeCommand(self._runtime_id),
            )
            self._require_ack(payload, "terminate")
        except EngineError:
            self._state = _RemoteRuntimeState.FAILED
            raise
        self._state = _RemoteRuntimeState.TERMINATED

    def close(self) -> None:
        if self._state is _RemoteRuntimeState.CLOSED:
            return
        try:
            payload = self._client.request(
                WorkerMessageType.CLOSE,
                RuntimeCommand(self._runtime_id),
            )
            self._require_ack(payload, "close")
        finally:
            self._state = _RemoteRuntimeState.CLOSED

    def _ensure_initialized(self, error_code: ErrorCode, command: str) -> None:
        if self._state is not _RemoteRuntimeState.INITIALIZED:
            raise self._lifecycle_error(error_code, command)

    def _require_ack(self, payload: object | None, command: str) -> None:
        if not isinstance(payload, RuntimeAck) or payload.runtime_id != self._runtime_id:
            raise self._unexpected_payload_error(command)

    def _lifecycle_error(self, error_code: ErrorCode, command: str) -> EngineError:
        return EngineError(
            error_code,
            "Remote node runtime 状态不允许该操作",
            {
                "phase": "remote_runtime_lifecycle",
                "worker_id": self._client.worker_id,
                "runtime_id": self._runtime_id,
                "state": self._state.value,
                "command": command,
            },
        )

    def _unexpected_payload_error(self, command: str) -> EngineError:
        return EngineError(
            ErrorCode.INTERNAL_ERROR,
            "Worker runtime 响应 payload 无效",
            {
                "phase": "remote_runtime_response",
                "worker_id": self._client.worker_id,
                "runtime_id": self._runtime_id,
                "command": command,
                "issue_code": "UNEXPECTED_WORKER_PAYLOAD",
            },
        )
