from __future__ import annotations

import unittest

from farcel.application.worker_client import WorkerRpcClient
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.worker_protocol import (
    RemoteError,
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
        self.binary_payloads: list[bytes | None] = []

    def connect(self) -> None:
        self.connect_count += 1

    def request(
        self,
        request: WorkerRequest,
        *,
        binary_payload: bytes | None = None,
    ) -> WorkerResponse:
        self.requests.append(request)
        self.binary_payloads.append(binary_payload)
        outcome = self._responder(request, binary_payload)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self) -> None:
        self.close_count += 1


def _success(request: WorkerRequest, payload: object | None = None) -> WorkerResponse:
    return WorkerResponse(
        request.request_id,
        request.worker_id,
        request.message_type,
        True,
        payload,
    )


class WorkerRpcClientTests(unittest.TestCase):
    def test_blank_worker_id_is_rejected(self) -> None:
        transport = _Transport(_success)
        for worker_id in ("", "   ", None):
            with self.subTest(worker_id=worker_id):
                with self.assertRaises(EngineError) as raised:
                    WorkerRpcClient(worker_id, transport)  # type: ignore[arg-type]
                self.assertIs(raised.exception.code, ErrorCode.VALIDATION_ERROR)

    def test_connect_success_request_and_binary_payload_delegate_once(self) -> None:
        transport = _Transport(lambda request, _: _success(request, {"value": 1}))
        client = WorkerRpcClient(
            "worker-a",
            transport,
            request_id_factory=lambda: "request-1",
        )

        client.connect()
        payload = client.request(WorkerMessageType.PING, {"ignored": True}, binary_payload=b"asset")
        client.close()

        self.assertEqual((transport.connect_count, transport.close_count), (1, 1))
        self.assertEqual(len(transport.requests), 1)
        request = transport.requests[0]
        self.assertEqual(
            (request.request_id, request.worker_id, request.message_type, request.payload),
            ("request-1", "worker-a", WorkerMessageType.PING, {"ignored": True}),
        )
        self.assertEqual(transport.binary_payloads, [b"asset"])
        self.assertEqual(payload, {"value": 1})

    def test_invalid_request_id_factory_result_does_not_call_transport(self) -> None:
        for value in (None, "", "   ", 3):
            with self.subTest(value=value):
                transport = _Transport(_success)
                client = WorkerRpcClient("worker-a", transport, request_id_factory=lambda: value)
                with self.assertRaises(EngineError) as raised:
                    client.request(WorkerMessageType.PING)
                self.assertIs(raised.exception.code, ErrorCode.INTERNAL_ERROR)
                self.assertEqual(raised.exception.details["phase"], "worker_rpc_request_id")
                self.assertEqual(transport.requests, [])

    def test_remote_error_preserves_engine_error_fields(self) -> None:
        def responder(request: WorkerRequest, _: bytes | None) -> WorkerResponse:
            return WorkerResponse(
                request.request_id,
                request.worker_id,
                request.message_type,
                False,
                error=RemoteError(ErrorCode.STEP_ERROR, "remote step failed", {"phase": "remote", "foo": "bar"}),
            )

        client = WorkerRpcClient("worker-a", _Transport(responder), request_id_factory=lambda: "request")
        with self.assertRaises(EngineError) as raised:
            client.request(WorkerMessageType.ADVANCE_TO)
        self.assertEqual(
            (raised.exception.code, raised.exception.message, raised.exception.details),
            (ErrorCode.STEP_ERROR, "remote step failed", {"phase": "remote", "foo": "bar"}),
        )

    def test_failed_response_without_error_is_stable_internal_error(self) -> None:
        transport = _Transport(
            lambda request, _: WorkerResponse(
                request.request_id,
                request.worker_id,
                request.message_type,
                False,
            )
        )
        client = WorkerRpcClient("worker-a", transport, request_id_factory=lambda: "request")
        with self.assertRaises(EngineError) as raised:
            client.request(WorkerMessageType.PING)
        self.assertIs(raised.exception.code, ErrorCode.INTERNAL_ERROR)
        self.assertEqual(raised.exception.details["issue_code"], "MISSING_REMOTE_ERROR")

    def test_transport_failure_is_not_retried(self) -> None:
        transport = _Transport(
            lambda _request, _binary: EngineError(ErrorCode.TIMEOUT, "transport timeout")
        )
        client = WorkerRpcClient("worker-a", transport, request_id_factory=lambda: "request")
        with self.assertRaises(EngineError) as raised:
            client.request(WorkerMessageType.PING)
        self.assertIs(raised.exception.code, ErrorCode.TIMEOUT)
        self.assertEqual(len(transport.requests), 1)
