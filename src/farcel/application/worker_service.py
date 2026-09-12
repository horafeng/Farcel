"""不依赖传输的 Worker request executor 与 runtime registry。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any
from uuid import uuid4

from farcel.application.node_runtime import ModelNodeRuntime
from farcel.application.worker_protocol import WorkerProtocolValidator
from farcel.application.worker_runtime_factory import WorkerRuntimeFactory
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.ports import WorkerAssetStore
from farcel.contracts.worker_protocol import (
    AdvanceToRequest,
    CreateRuntimeRequest,
    CreateRuntimeResponse,
    HasAssetRequest,
    HasAssetResponse,
    PutAssetRequest,
    PutAssetResponse,
    ReadOutputsResponse,
    RuntimeAck,
    RuntimeCommand,
    SetInputsRequest,
    WorkerMessageType,
    WorkerRequest,
    WorkerResponse,
    remote_error_from_engine_error,
)


_RUNTIME_ID_ATTEMPT_LIMIT = 16


class WorkerRuntimeState(str, Enum):
    CREATED = "created"
    INITIALIZED = "initialized"
    FAILED = "failed"
    TERMINATED = "terminated"


@dataclass(slots=True)
class _RuntimeRecord:
    runtime: ModelNodeRuntime
    node_id: str
    state: WorkerRuntimeState


class WorkerApplicationService:
    """执行已经解码的 Worker DTO，不管理 socket、连接或 graph。"""

    def __init__(
        self,
        worker_id: str,
        asset_store: WorkerAssetStore,
        runtime_factory: WorkerRuntimeFactory,
        *,
        runtime_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise EngineError(
                ErrorCode.VALIDATION_ERROR,
                "worker_id 必须是非空字符串",
                {"phase": "worker_identity"},
            )
        self._worker_id = worker_id
        self._asset_store = asset_store
        self._runtime_factory = runtime_factory
        self._runtime_id_factory = runtime_id_factory or (lambda: uuid4().hex)
        self._validator = WorkerProtocolValidator()
        self._runtimes: dict[str, _RuntimeRecord] = {}

    def handle_request(
        self,
        request: WorkerRequest,
        *,
        binary_payload: bytes | None = None,
    ) -> WorkerResponse:
        report = self._validator.validate_request(request)
        if not report.is_valid:
            raise EngineError(
                ErrorCode.VALIDATION_ERROR,
                "Worker request validation failed",
                {
                    "phase": "worker_request_validation",
                    "issues": tuple(
                        {"field": issue.field, "code": issue.code, "message": issue.message}
                        for issue in report.issues
                    ),
                },
            )

        try:
            if request.worker_id != self._worker_id:
                raise EngineError(
                    ErrorCode.VALIDATION_ERROR,
                    "Worker identity 不匹配",
                    {
                        "phase": "worker_identity",
                        "expected_worker_id": self._worker_id,
                        "request_worker_id": request.worker_id,
                    },
                )
            if (
                request.message_type is not WorkerMessageType.PUT_ASSET
                and binary_payload is not None
            ):
                raise EngineError(
                    ErrorCode.VALIDATION_ERROR,
                    "仅 PUT_ASSET 允许 binary payload",
                    {"phase": "worker_request_binary_payload"},
                )
            payload = self._dispatch(request, binary_payload)
            response = WorkerResponse(
                request.request_id,
                self._worker_id,
                request.message_type,
                True,
                payload,
            )
        except EngineError as error:
            response = WorkerResponse(
                request.request_id,
                self._worker_id,
                request.message_type,
                False,
                error=remote_error_from_engine_error(error),
            )
        except Exception as exc:
            response = WorkerResponse(
                request.request_id,
                self._worker_id,
                request.message_type,
                False,
                error=remote_error_from_engine_error(
                    EngineError(
                        ErrorCode.INTERNAL_ERROR,
                        "Worker request 处理失败",
                        {
                            "phase": "worker_request",
                            "message_type": request.message_type.value,
                            "diagnostic": str(exc),
                        },
                    )
                ),
            )

        if not self._validator.validate_response(response).is_valid:
            raise EngineError(ErrorCode.INTERNAL_ERROR, "Worker response 不符合协议语义")
        return response

    def shutdown(self) -> None:
        failures: list[dict[str, Any]] = []
        for runtime_id, record in tuple(self._runtimes.items()):
            if record.state is not WorkerRuntimeState.TERMINATED:
                try:
                    record.runtime.terminate()
                    record.state = WorkerRuntimeState.TERMINATED
                except Exception as exc:
                    record.state = WorkerRuntimeState.FAILED
                    failures.append({"runtime_id": runtime_id, "command": "terminate", "diagnostic": str(exc)})
            try:
                record.runtime.close()
            except Exception as exc:
                failures.append({"runtime_id": runtime_id, "command": "close", "diagnostic": str(exc)})
            finally:
                self._runtimes.pop(runtime_id, None)
        if failures:
            raise EngineError(
                ErrorCode.CLEANUP_ERROR,
                "Worker shutdown cleanup 失败",
                {"cleanup_failures": tuple(failures)},
            )

    def _dispatch(self, request: WorkerRequest, binary_payload: bytes | None) -> object | None:
        payload = request.payload
        if request.message_type in {WorkerMessageType.HELLO, WorkerMessageType.PING}:
            return None
        if request.message_type is WorkerMessageType.HAS_ASSET:
            assert isinstance(payload, HasAssetRequest)
            return HasAssetResponse(payload.sha256, self._asset_store.has_asset(payload.sha256))
        if request.message_type is WorkerMessageType.PUT_ASSET:
            assert isinstance(payload, PutAssetRequest)
            self._put_asset(payload, binary_payload)
            return PutAssetResponse(payload.sha256)
        if request.message_type is WorkerMessageType.CREATE_RUNTIME:
            assert isinstance(payload, CreateRuntimeRequest)
            return self._create_runtime(payload)
        if request.message_type is WorkerMessageType.INITIALIZE:
            assert isinstance(payload, RuntimeCommand)
            return self._initialize(payload.runtime_id)
        if request.message_type is WorkerMessageType.SET_INPUTS:
            assert isinstance(payload, SetInputsRequest)
            return self._set_inputs(payload)
        if request.message_type is WorkerMessageType.ADVANCE_TO:
            assert isinstance(payload, AdvanceToRequest)
            return self._advance_to(payload)
        if request.message_type is WorkerMessageType.READ_OUTPUTS:
            assert isinstance(payload, RuntimeCommand)
            return self._read_outputs(payload.runtime_id)
        if request.message_type is WorkerMessageType.TERMINATE:
            assert isinstance(payload, RuntimeCommand)
            return self._terminate(payload.runtime_id)
        if request.message_type is WorkerMessageType.CLOSE:
            assert isinstance(payload, RuntimeCommand)
            return self._close(payload.runtime_id)
        raise EngineError(ErrorCode.VALIDATION_ERROR, "未知 Worker message_type")

    def _put_asset(self, request: PutAssetRequest, binary_payload: object) -> None:
        if not isinstance(binary_payload, bytes) or len(binary_payload) != request.size:
            raise EngineError(
                ErrorCode.VALIDATION_ERROR,
                "PUT_ASSET binary payload 与声明大小不一致",
                {
                    "phase": "worker_asset_upload",
                    "sha256": request.sha256,
                    "declared_size": request.size,
                    "actual_size": len(binary_payload) if isinstance(binary_payload, bytes) else None,
                },
            )
        self._asset_store.put_asset(request.sha256, binary_payload)

    def _create_runtime(self, request: CreateRuntimeRequest) -> CreateRuntimeResponse:
        runtime = self._runtime_factory.create(request)
        try:
            runtime_id = self._allocate_runtime_id()
            self._runtimes[runtime_id] = _RuntimeRecord(runtime, request.node_id, WorkerRuntimeState.CREATED)
        except Exception:
            try:
                runtime.close()
            except Exception:
                pass
            raise
        return CreateRuntimeResponse(runtime_id)

    def _initialize(self, runtime_id: str) -> RuntimeAck:
        record = self._runtime(runtime_id)
        self._require_state(record, runtime_id, "initialize", WorkerRuntimeState.CREATED, ErrorCode.INITIALIZATION_ERROR)
        self._run(record, "initialize", record.runtime.initialize)
        record.state = WorkerRuntimeState.INITIALIZED
        return RuntimeAck(runtime_id)

    def _set_inputs(self, request: SetInputsRequest) -> RuntimeAck:
        record = self._runtime(request.runtime_id)
        self._require_state(record, request.runtime_id, "set_inputs", WorkerRuntimeState.INITIALIZED, ErrorCode.INPUT_SET_ERROR)
        self._run(record, "set_inputs", lambda: record.runtime.set_inputs(request.values))
        return RuntimeAck(request.runtime_id)

    def _advance_to(self, request: AdvanceToRequest) -> RuntimeAck:
        record = self._runtime(request.runtime_id)
        self._require_state(record, request.runtime_id, "advance_to", WorkerRuntimeState.INITIALIZED, ErrorCode.STEP_ERROR)
        self._run(record, "advance_to", lambda: record.runtime.advance_to(request.target_time))
        return RuntimeAck(request.runtime_id)

    def _read_outputs(self, runtime_id: str) -> ReadOutputsResponse:
        record = self._runtime(runtime_id)
        self._require_state(record, runtime_id, "read_outputs", WorkerRuntimeState.INITIALIZED, ErrorCode.OUTPUT_READ_ERROR)
        outputs = self._run(record, "read_outputs", record.runtime.read_outputs)
        return ReadOutputsResponse(runtime_id, outputs)

    def _terminate(self, runtime_id: str) -> RuntimeAck:
        record = self._runtime(runtime_id)
        if record.state is WorkerRuntimeState.TERMINATED:
            return RuntimeAck(runtime_id)
        self._run(record, "terminate", record.runtime.terminate)
        record.state = WorkerRuntimeState.TERMINATED
        return RuntimeAck(runtime_id)

    def _close(self, runtime_id: str) -> RuntimeAck:
        record = self._runtime(runtime_id)
        try:
            record.runtime.close()
        finally:
            self._runtimes.pop(runtime_id, None)
        return RuntimeAck(runtime_id)

    def _runtime(self, runtime_id: str) -> _RuntimeRecord:
        record = self._runtimes.get(runtime_id)
        if record is None:
            raise EngineError(
                ErrorCode.VALIDATION_ERROR,
                "未知 runtime_id",
                {"phase": "worker_runtime_lookup", "runtime_id": runtime_id, "issue_code": "UNKNOWN_RUNTIME_ID"},
            )
        return record

    @staticmethod
    def _require_state(
        record: _RuntimeRecord,
        runtime_id: str,
        command: str,
        expected: WorkerRuntimeState,
        code: ErrorCode,
    ) -> None:
        if record.state is not expected:
            raise EngineError(
                code,
                "Worker runtime 生命周期状态不允许该命令",
                {"phase": "worker_runtime_lifecycle", "runtime_id": runtime_id, "state": record.state.value, "command": command},
            )

    @staticmethod
    def _run(record: _RuntimeRecord, command: str, action: Callable[[], Any]) -> Any:
        try:
            return action()
        except Exception:
            record.state = WorkerRuntimeState.FAILED
            raise

    def _allocate_runtime_id(self) -> str:
        for _ in range(_RUNTIME_ID_ATTEMPT_LIMIT):
            runtime_id = self._runtime_id_factory()
            if isinstance(runtime_id, str) and runtime_id.strip() and runtime_id not in self._runtimes:
                return runtime_id
        raise EngineError(
            ErrorCode.INTERNAL_ERROR,
            "无法生成唯一 runtime_id",
            {"phase": "worker_runtime_registry"},
        )
