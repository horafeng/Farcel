"""共享的 Worker 协议声明；不包含传输、编码或运行时实现。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import SimulationConfig


WORKER_PROTOCOL_VERSION = "1.0"


class WorkerMessageType(str, Enum):
    HELLO = "hello"
    PING = "ping"
    HAS_ASSET = "has_asset"
    PUT_ASSET = "put_asset"
    CREATE_RUNTIME = "create_runtime"
    INITIALIZE = "initialize"
    SET_INPUTS = "set_inputs"
    ADVANCE_TO = "advance_to"
    READ_OUTPUTS = "read_outputs"
    TERMINATE = "terminate"
    CLOSE = "close"


@dataclass(frozen=True, slots=True)
class RemoteError:
    code: ErrorCode
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkerRequest:
    request_id: str
    worker_id: str
    message_type: WorkerMessageType
    payload: object | None = None
    protocol_version: str = WORKER_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class WorkerResponse:
    request_id: str
    worker_id: str
    message_type: WorkerMessageType
    ok: bool
    payload: object | None = None
    error: RemoteError | None = None
    protocol_version: str = WORKER_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class HasAssetRequest:
    sha256: str


@dataclass(frozen=True, slots=True)
class PutAssetRequest:
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class CreateRuntimeRequest:
    node_id: str
    asset_sha256: str
    config: SimulationConfig


@dataclass(frozen=True, slots=True)
class RuntimeCommand:
    runtime_id: str


@dataclass(frozen=True, slots=True)
class SetInputsRequest:
    runtime_id: str
    values: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AdvanceToRequest:
    runtime_id: str
    target_time: float


@dataclass(frozen=True, slots=True)
class HasAssetResponse:
    sha256: str
    available: bool


@dataclass(frozen=True, slots=True)
class PutAssetResponse:
    sha256: str


@dataclass(frozen=True, slots=True)
class CreateRuntimeResponse:
    runtime_id: str


@dataclass(frozen=True, slots=True)
class RuntimeAck:
    runtime_id: str


@dataclass(frozen=True, slots=True)
class ReadOutputsResponse:
    runtime_id: str
    outputs: Mapping[str, Any]


def remote_error_from_engine_error(error: EngineError) -> RemoteError:
    """转换稳定错误字段，不传输 Python exception 实例。"""

    return RemoteError(error.code, error.message, error.details)


def engine_error_from_remote_error(error: RemoteError) -> EngineError:
    """恢复稳定的 Farcel `EngineError`，保留错误语义。"""

    return EngineError(error.code, error.message, error.details)
