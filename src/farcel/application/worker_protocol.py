"""Worker 协议 DTO 的纯语义验证；不执行传输、编码或生命周期管理。"""

from __future__ import annotations

import math
from collections.abc import Mapping

from farcel.contracts.errors import ErrorCode
from farcel.contracts.models import SimulationConfig, ValidationIssue, ValidationReport
from farcel.contracts.worker_protocol import (
    AdvanceToRequest,
    CreateRuntimeRequest,
    CreateRuntimeResponse,
    HasAssetRequest,
    HasAssetResponse,
    PutAssetRequest,
    PutAssetResponse,
    ReadOutputsResponse,
    RemoteError,
    RuntimeAck,
    RuntimeCommand,
    SetInputsRequest,
    WORKER_PROTOCOL_VERSION,
    WorkerMessageType,
    WorkerRequest,
    WorkerResponse,
)


_SHA256_LENGTH = 64
class WorkerProtocolValidator:
    """验证协议对象自身结构，不访问 Worker 或任何外部资源。"""

    def validate_request(self, request: WorkerRequest) -> ValidationReport:
        issues: list[ValidationIssue] = []
        self._validate_envelope(
            protocol_version=request.protocol_version,
            request_id=request.request_id,
            worker_id=request.worker_id,
            message_type=request.message_type,
            issues=issues,
        )
        if isinstance(request.message_type, WorkerMessageType):
            self._validate_request_payload(request.message_type, request.payload, issues)
        return ValidationReport(tuple(issues))

    def validate_response(
        self,
        response: WorkerResponse,
        *,
        request: WorkerRequest | None = None,
    ) -> ValidationReport:
        issues: list[ValidationIssue] = []
        self._validate_envelope(
            protocol_version=response.protocol_version,
            request_id=response.request_id,
            worker_id=response.worker_id,
            message_type=response.message_type,
            issues=issues,
        )
        if not isinstance(response.ok, bool):
            issues.append(
                _issue("ok", "INVALID_PROTOCOL_RESPONSE_STATE", "response.ok 必须是 bool")
            )
        elif response.ok:
            if response.error is not None:
                issues.append(
                    _issue("error", "INVALID_PROTOCOL_RESPONSE_STATE", "成功 response 不得包含 error")
                )
            if isinstance(response.message_type, WorkerMessageType):
                self._validate_response_payload(
                    response.message_type,
                    response.payload,
                    issues,
                )
        else:
            if response.payload is not None:
                issues.append(
                    _issue("payload", "INVALID_PROTOCOL_RESPONSE_STATE", "失败 response 的 payload 必须为 None")
                )
            if not isinstance(response.error, RemoteError):
                issues.append(
                    _issue("error", "INVALID_REMOTE_ERROR", "失败 response 必须包含 RemoteError")
                )
            else:
                self._validate_remote_error(response.error, issues)

        if request is not None:
            self._validate_correlation(response, request, issues)
        return ValidationReport(tuple(issues))

    @staticmethod
    def _validate_envelope(
        *,
        protocol_version: object,
        request_id: object,
        worker_id: object,
        message_type: object,
        issues: list[ValidationIssue],
    ) -> None:
        if protocol_version != WORKER_PROTOCOL_VERSION:
            issues.append(
                _issue("protocol_version", "INVALID_PROTOCOL_VERSION", "protocol_version 不受支持")
            )
        if _is_blank(request_id):
            issues.append(
                _issue("request_id", "INVALID_PROTOCOL_REQUEST_ID", "request_id 必须是非空字符串")
            )
        if _is_blank(worker_id):
            issues.append(
                _issue("worker_id", "INVALID_PROTOCOL_WORKER_ID", "worker_id 必须是非空字符串")
            )
        if not isinstance(message_type, WorkerMessageType):
            issues.append(
                _issue("message_type", "INVALID_PROTOCOL_MESSAGE_TYPE", "message_type 必须是 WorkerMessageType")
            )

    def _validate_request_payload(
        self,
        message_type: WorkerMessageType,
        payload: object | None,
        issues: list[ValidationIssue],
    ) -> None:
        expected_type = _request_payload_type(message_type)
        if expected_type is None:
            if payload is not None:
                issues.append(_invalid_payload_issue(message_type))
            return
        if type(payload) is not expected_type:
            issues.append(_invalid_payload_issue(message_type))
            return

        if isinstance(payload, HasAssetRequest):
            self._validate_sha256("payload.sha256", payload.sha256, issues)
        elif isinstance(payload, PutAssetRequest):
            self._validate_sha256("payload.sha256", payload.sha256, issues)
            if (
                not isinstance(payload.size, int)
                or isinstance(payload.size, bool)
                or payload.size < 0
            ):
                issues.append(_issue("payload.size", "INVALID_ASSET_SIZE", "asset size 必须是非负整数"))
        elif isinstance(payload, CreateRuntimeRequest):
            if _is_blank(payload.node_id):
                issues.append(_issue("payload.node_id", "INVALID_RUNTIME_NODE_ID", "node_id 必须是非空字符串"))
            self._validate_sha256("payload.asset_sha256", payload.asset_sha256, issues)
            if not isinstance(payload.config, SimulationConfig):
                issues.append(_invalid_payload_issue(message_type))
        elif isinstance(payload, RuntimeCommand):
            self._validate_runtime_id(payload.runtime_id, issues)
        elif isinstance(payload, SetInputsRequest):
            self._validate_runtime_id(payload.runtime_id, issues)
            if not isinstance(payload.values, Mapping):
                issues.append(_issue("payload.values", "INVALID_INPUT_VALUES", "values 必须是 Mapping"))
        elif isinstance(payload, AdvanceToRequest):
            self._validate_runtime_id(payload.runtime_id, issues)
            if (
                not isinstance(payload.target_time, (int, float))
                or isinstance(payload.target_time, bool)
                or not math.isfinite(payload.target_time)
            ):
                issues.append(_issue("payload.target_time", "INVALID_TARGET_TIME", "target_time 必须是有限数值"))

    def _validate_response_payload(
        self,
        message_type: WorkerMessageType,
        payload: object | None,
        issues: list[ValidationIssue],
    ) -> None:
        expected_type = _response_payload_type(message_type)
        if expected_type is None:
            if payload is not None:
                issues.append(_invalid_payload_issue(message_type))
            return
        if type(payload) is not expected_type:
            issues.append(_invalid_payload_issue(message_type))
            return

        if isinstance(payload, HasAssetResponse):
            self._validate_sha256("payload.sha256", payload.sha256, issues)
            if not isinstance(payload.available, bool):
                issues.append(_issue("payload.available", "INVALID_PROTOCOL_PAYLOAD", "available 必须是 bool"))
        elif isinstance(payload, PutAssetResponse):
            self._validate_sha256("payload.sha256", payload.sha256, issues)
        elif isinstance(payload, (CreateRuntimeResponse, RuntimeAck)):
            self._validate_runtime_id(payload.runtime_id, issues)
        elif isinstance(payload, ReadOutputsResponse):
            self._validate_runtime_id(payload.runtime_id, issues)
            if not isinstance(payload.outputs, Mapping):
                issues.append(_issue("payload.outputs", "INVALID_PROTOCOL_PAYLOAD", "outputs 必须是 Mapping"))

    @staticmethod
    def _validate_remote_error(error: RemoteError, issues: list[ValidationIssue]) -> None:
        if not isinstance(error.code, ErrorCode):
            issues.append(_issue("error.code", "INVALID_REMOTE_ERROR", "error.code 必须是 ErrorCode"))
        if _is_blank(error.message):
            issues.append(_issue("error.message", "INVALID_REMOTE_ERROR", "error.message 必须是非空字符串"))
        if not isinstance(error.details, Mapping):
            issues.append(_issue("error.details", "INVALID_REMOTE_ERROR", "error.details 必须是 Mapping"))

    @staticmethod
    def _validate_correlation(
        response: WorkerResponse,
        request: WorkerRequest,
        issues: list[ValidationIssue],
    ) -> None:
        comparisons = (
            ("request_id", response.request_id, request.request_id, "PROTOCOL_REQUEST_ID_MISMATCH"),
            ("worker_id", response.worker_id, request.worker_id, "PROTOCOL_WORKER_ID_MISMATCH"),
            ("message_type", response.message_type, request.message_type, "PROTOCOL_MESSAGE_TYPE_MISMATCH"),
            ("protocol_version", response.protocol_version, request.protocol_version, "PROTOCOL_VERSION_MISMATCH"),
        )
        for field, response_value, request_value, code in comparisons:
            if response_value != request_value:
                issues.append(_issue(field, code, "response 必须与 request 对应"))

    @staticmethod
    def _validate_sha256(field: str, value: object, issues: list[ValidationIssue]) -> None:
        if not _is_canonical_sha256(value):
            issues.append(_issue(field, "INVALID_ASSET_SHA256", "sha256 必须是 64 位小写十六进制"))

    @staticmethod
    def _validate_runtime_id(runtime_id: object, issues: list[ValidationIssue]) -> None:
        if _is_blank(runtime_id):
            issues.append(_issue("payload.runtime_id", "INVALID_RUNTIME_ID", "runtime_id 必须是非空字符串"))


def _request_payload_type(message_type: WorkerMessageType) -> type[object] | None:
    payload_types: dict[WorkerMessageType, type[object] | None] = {
        WorkerMessageType.HELLO: None,
        WorkerMessageType.PING: None,
        WorkerMessageType.HAS_ASSET: HasAssetRequest,
        WorkerMessageType.PUT_ASSET: PutAssetRequest,
        WorkerMessageType.CREATE_RUNTIME: CreateRuntimeRequest,
        WorkerMessageType.INITIALIZE: RuntimeCommand,
        WorkerMessageType.SET_INPUTS: SetInputsRequest,
        WorkerMessageType.ADVANCE_TO: AdvanceToRequest,
        WorkerMessageType.READ_OUTPUTS: RuntimeCommand,
        WorkerMessageType.TERMINATE: RuntimeCommand,
        WorkerMessageType.CLOSE: RuntimeCommand,
    }
    return payload_types[message_type]


def _response_payload_type(message_type: WorkerMessageType) -> type[object] | None:
    payload_types: dict[WorkerMessageType, type[object] | None] = {
        WorkerMessageType.HELLO: None,
        WorkerMessageType.PING: None,
        WorkerMessageType.HAS_ASSET: HasAssetResponse,
        WorkerMessageType.PUT_ASSET: PutAssetResponse,
        WorkerMessageType.CREATE_RUNTIME: CreateRuntimeResponse,
        WorkerMessageType.INITIALIZE: RuntimeAck,
        WorkerMessageType.SET_INPUTS: RuntimeAck,
        WorkerMessageType.ADVANCE_TO: RuntimeAck,
        WorkerMessageType.READ_OUTPUTS: ReadOutputsResponse,
        WorkerMessageType.TERMINATE: RuntimeAck,
        WorkerMessageType.CLOSE: RuntimeAck,
    }
    return payload_types[message_type]


def _is_canonical_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_blank(value: object) -> bool:
    return not isinstance(value, str) or not value.strip()


def _issue(field: str, code: str, message: str) -> ValidationIssue:
    return ValidationIssue(field, code, message)


def _invalid_payload_issue(message_type: WorkerMessageType) -> ValidationIssue:
    return _issue(
        "payload",
        "INVALID_PROTOCOL_PAYLOAD",
        f"{message_type.value} message 的 payload 类型不正确",
    )
