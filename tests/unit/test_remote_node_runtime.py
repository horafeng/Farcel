from __future__ import annotations

import math
import unittest

from farcel.application.remote_node_runtime import RemoteNodeRuntime
from farcel.application.worker_client import WorkerRpcClient
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.worker_protocol import (
    ReadOutputsResponse,
    RemoteError,
    RuntimeAck,
    WorkerMessageType,
    WorkerRequest,
    WorkerResponse,
)


class _Transport:
    def __init__(self, responder) -> None:
        self._responder = responder
        self.connect_count = 0
        self.close_count = 0
        self.requests: list[WorkerRequest] = []

    def connect(self) -> None:
        self.connect_count += 1

    def request(
        self,
        request: WorkerRequest,
        *,
        binary_payload: bytes | None = None,
    ) -> WorkerResponse:
        self.requests.append(request)
        outcome = self._responder(request, binary_payload)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self) -> None:
        self.close_count += 1


def _response(request: WorkerRequest, payload: object | None = None) -> WorkerResponse:
    return WorkerResponse(
        request.request_id,
        request.worker_id,
        request.message_type,
        True,
        payload,
    )


def _normal_responder(request: WorkerRequest, _: bytes | None) -> WorkerResponse:
    runtime_id = request.payload.runtime_id
    if request.message_type is WorkerMessageType.READ_OUTPUTS:
        return _response(request, ReadOutputsResponse(runtime_id, {"y": 2.0}))
    return _response(request, RuntimeAck(runtime_id))


class RemoteNodeRuntimeTests(unittest.TestCase):
    def _runtime(self, transport: _Transport, runtime_id: str = "runtime-a") -> RemoteNodeRuntime:
        request_ids = (f"request-{number}" for number in range(1, 100))
        client = WorkerRpcClient("worker-a", transport, request_id_factory=lambda: next(request_ids))
        return RemoteNodeRuntime(client, runtime_id)

    def test_constructor_requires_nonblank_runtime_id(self) -> None:
        transport = _Transport(_normal_responder)
        client = WorkerRpcClient("worker-a", transport)
        for runtime_id in ("", "   ", None):
            with self.subTest(runtime_id=runtime_id):
                with self.assertRaises(EngineError) as raised:
                    RemoteNodeRuntime(client, runtime_id)  # type: ignore[arg-type]
                self.assertIs(raised.exception.code, ErrorCode.VALIDATION_ERROR)

    def test_happy_path_matches_model_node_runtime_lifecycle(self) -> None:
        transport = _Transport(_normal_responder)
        runtime = self._runtime(transport)

        runtime.initialize()
        runtime.set_inputs({"u": 1.0})
        runtime.advance_to(0.1)
        outputs = runtime.read_outputs()
        runtime.terminate()
        runtime.close()

        self.assertEqual(outputs, {"y": 2.0})
        self.assertEqual(
            [request.message_type for request in transport.requests],
            [
                WorkerMessageType.INITIALIZE,
                WorkerMessageType.SET_INPUTS,
                WorkerMessageType.ADVANCE_TO,
                WorkerMessageType.READ_OUTPUTS,
                WorkerMessageType.TERMINATE,
                WorkerMessageType.CLOSE,
            ],
        )
        self.assertTrue(
            all(request.payload.runtime_id == "runtime-a" for request in transport.requests)
        )

    def test_initialize_empty_inputs_terminate_and_close_are_proxy_idempotent(self) -> None:
        transport = _Transport(_normal_responder)
        runtime = self._runtime(transport)

        runtime.initialize()
        runtime.initialize()
        runtime.set_inputs({})
        runtime.advance_to(0.1)
        runtime.terminate()
        runtime.terminate()
        runtime.close()
        runtime.close()

        self.assertEqual(
            [request.message_type for request in transport.requests],
            [
                WorkerMessageType.INITIALIZE,
                WorkerMessageType.ADVANCE_TO,
                WorkerMessageType.TERMINATE,
                WorkerMessageType.CLOSE,
            ],
        )

    def test_terminate_before_initialize_is_noop_but_close_is_sent(self) -> None:
        transport = _Transport(_normal_responder)
        runtime = self._runtime(transport)
        runtime.terminate()
        runtime.close()
        self.assertEqual([request.message_type for request in transport.requests], [WorkerMessageType.CLOSE])

    def test_invalid_advance_target_is_rejected_before_transport(self) -> None:
        for target_time in (True, "0.1", math.nan, math.inf, -math.inf):
            with self.subTest(target_time=target_time):
                transport = _Transport(_normal_responder)
                runtime = self._runtime(transport)
                runtime.initialize()
                with self.assertRaises(EngineError) as raised:
                    runtime.advance_to(target_time)  # type: ignore[arg-type]
                self.assertIs(raised.exception.code, ErrorCode.STEP_ERROR)
                self.assertEqual(len(transport.requests), 1)

    def test_business_failure_marks_failed_then_cleanup_remains_allowed(self) -> None:
        def responder(request: WorkerRequest, _: bytes | None) -> WorkerResponse:
            if request.message_type is WorkerMessageType.ADVANCE_TO:
                return WorkerResponse(
                    request.request_id,
                    request.worker_id,
                    request.message_type,
                    False,
                    error=RemoteError(ErrorCode.STEP_ERROR, "remote step failed", {"foo": "bar"}),
                )
            return _normal_responder(request, _)

        transport = _Transport(responder)
        runtime = self._runtime(transport)
        runtime.initialize()
        with self.assertRaises(EngineError) as raised:
            runtime.advance_to(0.1)
        self.assertEqual(
            (raised.exception.code, raised.exception.message, raised.exception.details),
            (ErrorCode.STEP_ERROR, "remote step failed", {"foo": "bar"}),
        )
        request_count = len(transport.requests)
        with self.assertRaises(EngineError) as rejected:
            runtime.read_outputs()
        self.assertIs(rejected.exception.code, ErrorCode.OUTPUT_READ_ERROR)
        self.assertEqual(len(transport.requests), request_count)
        runtime.terminate()
        runtime.close()
        self.assertEqual(
            [request.message_type for request in transport.requests[-2:]],
            [WorkerMessageType.TERMINATE, WorkerMessageType.CLOSE],
        )

    def test_transport_ambiguity_is_not_replayed(self) -> None:
        def responder(request: WorkerRequest, _: bytes | None) -> WorkerResponse | Exception:
            if request.message_type is WorkerMessageType.ADVANCE_TO:
                return EngineError(ErrorCode.TIMEOUT, "request outcome unknown")
            return _normal_responder(request, _)

        transport = _Transport(responder)
        runtime = self._runtime(transport)
        runtime.initialize()
        with self.assertRaises(EngineError) as raised:
            runtime.advance_to(0.1)
        self.assertIs(raised.exception.code, ErrorCode.TIMEOUT)
        self.assertEqual(len(transport.requests), 2)
        with self.assertRaises(EngineError) as rejected:
            runtime.advance_to(0.1)
        self.assertIs(rejected.exception.code, ErrorCode.STEP_ERROR)
        self.assertEqual(len(transport.requests), 2)
        runtime.close()
        self.assertEqual(len(transport.requests), 3)

    def test_unexpected_payloads_are_stable_internal_errors(self) -> None:
        cases = (
            (
                "initialize_type",
                lambda request: _response(request, ReadOutputsResponse("runtime-a", {})),
                "initialize",
            ),
            (
                "initialize_runtime_id",
                lambda request: _response(request, RuntimeAck("other-runtime")),
                "initialize",
            ),
            (
                "read_type",
                lambda request: _response(request, RuntimeAck("runtime-a")),
                "read",
            ),
            (
                "read_runtime_id",
                lambda request: _response(request, ReadOutputsResponse("other-runtime", {})),
                "read",
            ),
        )
        for name, payload_factory, action in cases:
            with self.subTest(name=name):
                def responder(request: WorkerRequest, _: bytes | None) -> WorkerResponse:
                    if action == "read" and request.message_type is WorkerMessageType.INITIALIZE:
                        return _normal_responder(request, _)
                    return payload_factory(request)

                transport = _Transport(responder)
                runtime = self._runtime(transport)
                if action == "read":
                    runtime.initialize()
                    operation = runtime.read_outputs
                else:
                    operation = runtime.initialize
                with self.assertRaises(EngineError) as raised:
                    operation()
                self.assertIs(raised.exception.code, ErrorCode.INTERNAL_ERROR)
                self.assertEqual(raised.exception.details["issue_code"], "UNEXPECTED_WORKER_PAYLOAD")

    def test_close_failure_is_not_replayed_and_marks_runtime_closed(self) -> None:
        def responder(request: WorkerRequest, _: bytes | None) -> WorkerResponse:
            if request.message_type is WorkerMessageType.CLOSE:
                return WorkerResponse(
                    request.request_id,
                    request.worker_id,
                    request.message_type,
                    False,
                    error=RemoteError(ErrorCode.CLEANUP_ERROR, "close failed", {}),
                )
            return _normal_responder(request, _)

        transport = _Transport(responder)
        runtime = self._runtime(transport)
        with self.assertRaises(EngineError) as raised:
            runtime.close()
        self.assertIs(raised.exception.code, ErrorCode.CLEANUP_ERROR)
        runtime.close()
        self.assertEqual([request.message_type for request in transport.requests], [WorkerMessageType.CLOSE])
        with self.assertRaises(EngineError) as rejected:
            runtime.read_outputs()
        self.assertIs(rejected.exception.code, ErrorCode.OUTPUT_READ_ERROR)

    def test_shared_client_survives_one_runtime_close(self) -> None:
        transport = _Transport(_normal_responder)
        request_ids = (f"request-{number}" for number in range(1, 100))
        client = WorkerRpcClient("worker-a", transport, request_id_factory=lambda: next(request_ids))
        first = RemoteNodeRuntime(client, "runtime-a")
        second = RemoteNodeRuntime(client, "runtime-b")

        first.close()
        self.assertEqual(transport.close_count, 0)
        second.initialize()
        self.assertEqual(second.read_outputs(), {"y": 2.0})
        self.assertEqual(transport.close_count, 0)
