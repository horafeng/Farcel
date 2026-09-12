from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import unittest

from farcel.application.worker_service import WorkerApplicationService
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import SimulationConfig
from farcel.contracts.worker_protocol import (
    AdvanceToRequest,
    CreateRuntimeRequest,
    HasAssetRequest,
    PutAssetRequest,
    RuntimeCommand,
    SetInputsRequest,
    WorkerMessageType,
    WorkerRequest,
)


_SHA = "a" * 64


class _AssetStore:
    def __init__(self) -> None:
        self.available = False
        self.puts: list[tuple[str, bytes]] = []

    def has_asset(self, sha256: str) -> bool:
        return self.available

    def put_asset(self, sha256: str, content: bytes) -> None:
        self.puts.append((sha256, content))

    def resolve_asset(self, sha256: str) -> Path:
        return Path("worker-cache/assets") / f"{sha256}.fmu"


class _Runtime:
    def __init__(self) -> None:
        self.calls: list[object] = []
        self.outputs: Mapping[str, object] = {"y": 1.0}
        self.failure: Exception | None = None

    def _call(self, value: object) -> None:
        self.calls.append(value)
        if self.failure is not None:
            raise self.failure

    def initialize(self) -> None: self._call("initialize")
    def set_inputs(self, values: Mapping[str, object]) -> None: self._call(("set_inputs", values))
    def advance_to(self, target_time: float) -> None: self._call(("advance_to", target_time))
    def read_outputs(self) -> Mapping[str, object]: self._call("read_outputs"); return self.outputs
    def terminate(self) -> None: self._call("terminate")
    def close(self) -> None: self._call("close")


class _RuntimeFactory:
    def __init__(self, runtimes: list[_Runtime]) -> None:
        self.runtimes = runtimes
        self.requests: list[CreateRuntimeRequest] = []
        self.created: list[_Runtime] = []

    def create(self, request: CreateRuntimeRequest) -> _Runtime:
        self.requests.append(request)
        runtime = self.runtimes.pop(0)
        self.created.append(runtime)
        return runtime


class WorkerApplicationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _AssetStore()
        self.runtimes = [_Runtime(), _Runtime(), _Runtime(), _Runtime()]
        self.factory = _RuntimeFactory(self.runtimes)
        ids = iter(("runtime-1", "runtime-2", "runtime-3", "runtime-4"))
        self.service = WorkerApplicationService("worker-a", self.store, self.factory, runtime_id_factory=lambda: next(ids))

    def test_hello_ping_identity_and_asset_requests(self) -> None:
        for message_type in (WorkerMessageType.HELLO, WorkerMessageType.PING):
            response = self._handle(message_type)
            self.assertTrue(response.ok)
            self.assertIsNone(response.payload)
            self.assertEqual((response.request_id, response.worker_id, response.message_type, response.protocol_version), ("request", "worker-a", message_type, "1.0"))

        self.store.available = True
        response = self._handle(WorkerMessageType.HAS_ASSET, HasAssetRequest(_SHA))
        self.assertTrue(response.payload.available)
        response = self.service.handle_request(WorkerRequest("request", "other", WorkerMessageType.PING))
        self.assertFalse(response.ok)
        self.assertEqual((response.worker_id, response.error.code), ("worker-a", ErrorCode.VALIDATION_ERROR))

    def test_put_asset_requires_matching_binary_sidecar_and_non_put_rejects_binary(self) -> None:
        content = b"asset"
        response = self._handle(WorkerMessageType.PUT_ASSET, PutAssetRequest(_SHA, len(content)), binary_payload=content)
        self.assertTrue(response.ok)
        self.assertEqual(self.store.puts, [(_SHA, content)])
        response = self._handle(WorkerMessageType.PUT_ASSET, PutAssetRequest(_SHA, 4), binary_payload=content)
        self.assertFalse(response.ok)
        self.assertEqual(self.store.puts, [(_SHA, content)])
        response = self._handle(WorkerMessageType.PING, binary_payload=b"unexpected")
        self.assertFalse(response.ok)
        self.assertEqual(response.error.details["phase"], "worker_request_binary_payload")

    def test_runtime_lifecycle_errors_cleanup_and_unknown_runtime(self) -> None:
        runtime = self._create()
        response = self._handle(WorkerMessageType.SET_INPUTS, SetInputsRequest("runtime-1", {"u": 1.0}))
        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, ErrorCode.INPUT_SET_ERROR)
        self.assertTrue(self._handle(WorkerMessageType.INITIALIZE, RuntimeCommand("runtime-1")).ok)
        self.assertFalse(self._handle(WorkerMessageType.INITIALIZE, RuntimeCommand("runtime-1")).ok)
        self.assertTrue(self._handle(WorkerMessageType.SET_INPUTS, SetInputsRequest("runtime-1", {"u": 1.0})).ok)
        self.assertTrue(self._handle(WorkerMessageType.ADVANCE_TO, AdvanceToRequest("runtime-1", 0.2)).ok)
        response = self._handle(WorkerMessageType.READ_OUTPUTS, RuntimeCommand("runtime-1"))
        self.assertEqual(response.payload.outputs, {"y": 1.0})
        self.assertTrue(self._handle(WorkerMessageType.TERMINATE, RuntimeCommand("runtime-1")).ok)
        self.assertTrue(self._handle(WorkerMessageType.TERMINATE, RuntimeCommand("runtime-1")).ok)
        self.assertEqual(runtime.calls.count("terminate"), 1)
        self.assertTrue(self._handle(WorkerMessageType.CLOSE, RuntimeCommand("runtime-1")).ok)
        response = self._handle(WorkerMessageType.READ_OUTPUTS, RuntimeCommand("runtime-1"))
        self.assertFalse(response.ok)
        self.assertEqual(response.error.details["issue_code"], "UNKNOWN_RUNTIME_ID")

    def test_runtime_failure_becomes_remote_error_and_allows_cleanup(self) -> None:
        runtime = self._create()
        runtime.failure = EngineError(ErrorCode.INITIALIZATION_ERROR, "init failed", {"x": 1})
        response = self._handle(WorkerMessageType.INITIALIZE, RuntimeCommand("runtime-1"))
        self.assertFalse(response.ok)
        self.assertEqual((response.error.code, response.error.message, response.error.details), (ErrorCode.INITIALIZATION_ERROR, "init failed", {"x": 1}))
        runtime.failure = None
        self.assertTrue(self._handle(WorkerMessageType.TERMINATE, RuntimeCommand("runtime-1")).ok)
        self.assertTrue(self._handle(WorkerMessageType.CLOSE, RuntimeCommand("runtime-1")).ok)

    def test_created_and_failed_runtimes_allow_terminate_but_not_execution_commands(self) -> None:
        created = self._create()
        self.assertTrue(self._handle(WorkerMessageType.TERMINATE, RuntimeCommand("runtime-1")).ok)
        self.assertEqual(created.calls, ["terminate"])

        failed = self._create()
        failed.failure = EngineError(ErrorCode.INITIALIZATION_ERROR, "failed")
        self.assertFalse(self._handle(WorkerMessageType.INITIALIZE, RuntimeCommand("runtime-2")).ok)
        self.assertFalse(self._handle(WorkerMessageType.ADVANCE_TO, AdvanceToRequest("runtime-2", 0.2)).ok)
        failed.failure = None
        self.assertTrue(self._handle(WorkerMessageType.TERMINATE, RuntimeCommand("runtime-2")).ok)

    def test_set_advance_and_read_errors_mark_runtime_failed(self) -> None:
        for message_type, payload in (
            (WorkerMessageType.SET_INPUTS, SetInputsRequest("runtime-1", {"u": 1.0})),
            (WorkerMessageType.ADVANCE_TO, AdvanceToRequest("runtime-1", 0.2)),
            (WorkerMessageType.READ_OUTPUTS, RuntimeCommand("runtime-1")),
        ):
            with self.subTest(message_type=message_type):
                runtime = self._create()
                self.assertTrue(self._handle(WorkerMessageType.INITIALIZE, RuntimeCommand("runtime-1")).ok)
                runtime.failure = EngineError(ErrorCode.STEP_ERROR, "operation failed")
                response = self._handle(message_type, payload)
                self.assertFalse(response.ok)
                runtime.failure = None
                self.assertFalse(self._handle(WorkerMessageType.SET_INPUTS, SetInputsRequest("runtime-1", {"u": 1.0})).ok)
                self.assertTrue(self._handle(WorkerMessageType.CLOSE, RuntimeCommand("runtime-1")).ok)
                self.setUp()

    def test_unexpected_runtime_error_is_internal_and_close_always_removes_record(self) -> None:
        runtime = self._create()
        runtime.failure = RuntimeError("unexpected")
        response = self._handle(WorkerMessageType.INITIALIZE, RuntimeCommand("runtime-1"))
        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, ErrorCode.INTERNAL_ERROR)
        runtime.failure = RuntimeError("close failed")
        response = self._handle(WorkerMessageType.CLOSE, RuntimeCommand("runtime-1"))
        self.assertFalse(response.ok)
        response = self._handle(WorkerMessageType.CLOSE, RuntimeCommand("runtime-1"))
        self.assertFalse(response.ok)
        self.assertEqual(response.error.details["issue_code"], "UNKNOWN_RUNTIME_ID")

    def test_duplicate_runtime_id_does_not_overwrite_existing_runtime_and_shutdown_cleans_all(self) -> None:
        first = self._create()
        collision = WorkerApplicationService("worker-a", self.store, self.factory, runtime_id_factory=lambda: "runtime-1")
        response = collision.handle_request(self._request(WorkerMessageType.CREATE_RUNTIME, CreateRuntimeRequest("n", _SHA, SimulationConfig())))
        self.assertTrue(response.ok)
        response = collision.handle_request(self._request(WorkerMessageType.CREATE_RUNTIME, CreateRuntimeRequest("n", _SHA, SimulationConfig())))
        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, ErrorCode.INTERNAL_ERROR)
        self.assertIn("close", self.factory.created[-1].calls)
        self.assertTrue(self._handle(WorkerMessageType.CREATE_RUNTIME, CreateRuntimeRequest("n", _SHA, SimulationConfig())).ok)
        self.service.shutdown()
        self.assertIn("terminate", first.calls)
        self.assertIn("close", first.calls)

    def test_shutdown_attempts_every_runtime_even_when_cleanup_fails(self) -> None:
        first = self._create()
        second = self._create()
        first.failure = EngineError(ErrorCode.CLEANUP_ERROR, "terminate failed")
        with self.assertRaises(EngineError) as raised:
            self.service.shutdown()
        self.assertEqual(raised.exception.code, ErrorCode.CLEANUP_ERROR)
        self.assertIn("close", first.calls)
        self.assertIn("terminate", second.calls)
        self.assertIn("close", second.calls)

    def test_structurally_invalid_request_raises_engine_error(self) -> None:
        with self.assertRaises(EngineError) as raised:
            self.service.handle_request(WorkerRequest(" ", "worker-a", WorkerMessageType.PING))
        self.assertEqual((raised.exception.code, raised.exception.details["phase"]), (ErrorCode.VALIDATION_ERROR, "worker_request_validation"))

    def _create(self) -> _Runtime:
        response = self._handle(WorkerMessageType.CREATE_RUNTIME, CreateRuntimeRequest("node", _SHA, SimulationConfig()))
        self.assertTrue(response.ok)
        return self.factory.created[-1]

    def _handle(self, message_type: WorkerMessageType, payload=None, *, binary_payload=None):
        return self.service.handle_request(self._request(message_type, payload), binary_payload=binary_payload)

    @staticmethod
    def _request(message_type: WorkerMessageType, payload=None) -> WorkerRequest:
        return WorkerRequest("request", "worker-a", message_type, payload)
