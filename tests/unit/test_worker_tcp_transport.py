from __future__ import annotations

import socket
from threading import Event, Thread
import unittest

from farcel.application.worker_protocol import WorkerProtocolValidator
from farcel.contracts.distributed import WorkerEndpoint
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.worker_protocol import (
    HasAssetRequest,
    HasAssetResponse,
    PutAssetRequest,
    PutAssetResponse,
    RemoteError,
    WorkerMessageType,
    WorkerRequest,
    WorkerResponse,
)
from farcel.infrastructure.worker_protocol.framing import MAX_CONTROL_FRAME_BYTES, encode_frame
from farcel.infrastructure.worker_protocol.json_codec import JsonWorkerProtocolCodec
from farcel.infrastructure.worker_protocol.tcp_transport import TcpWorkerClient, TcpWorkerServer


_SHA = "a" * 64


class _Handler:
    def __init__(self, worker_id: str = "worker-a") -> None:
        self.worker_id = worker_id
        self.requests: list[tuple[WorkerRequest, bytes | None]] = []
        self.shutdown_count = 0

    def handle_request(self, request: WorkerRequest, *, binary_payload: bytes | None = None) -> WorkerResponse:
        self.requests.append((request, binary_payload))
        if request.worker_id != self.worker_id:
            return WorkerResponse(request.request_id, self.worker_id, request.message_type, False, error=RemoteError(ErrorCode.VALIDATION_ERROR, "identity"))
        if request.message_type is WorkerMessageType.HAS_ASSET:
            return WorkerResponse(request.request_id, self.worker_id, request.message_type, True, HasAssetResponse(request.payload.sha256, True))
        if request.message_type is WorkerMessageType.PUT_ASSET:
            return WorkerResponse(request.request_id, self.worker_id, request.message_type, True, PutAssetResponse(request.payload.sha256))
        return WorkerResponse(request.request_id, self.worker_id, request.message_type, True)

    def shutdown(self) -> None:
        self.shutdown_count += 1


class WorkerTcpTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        validator = WorkerProtocolValidator()
        self.codec = JsonWorkerProtocolCodec(validator)
        self.handler = _Handler()
        self.server = TcpWorkerServer("worker-a", self.handler, self.codec, handshake_timeout=0.5, operation_timeout=0.5)
        self.endpoint = self.server.start()
        self.stop = Event()
        self.thread = Thread(target=self.server.serve_forever, kwargs={"stop_event": self.stop})
        self.thread.start()

    def tearDown(self) -> None:
        self.stop.set()
        self.server.close()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())

    def _client(self, worker_id: str = "worker-a") -> TcpWorkerClient:
        return TcpWorkerClient(worker_id, self.endpoint, self.codec, WorkerProtocolValidator(), connect_timeout=0.5, handshake_timeout=0.5, operation_timeout=0.5, request_id_factory=lambda: "id")

    def test_loopback_ephemeral_persistent_handshake_and_asset_sidecar(self) -> None:
        self.assertEqual(self.endpoint.host, "127.0.0.1")
        self.assertGreater(self.endpoint.port, 0)
        client = self._client()
        try:
            client.connect()
            self.assertTrue(client.request(WorkerRequest("ping-1", "worker-a", WorkerMessageType.PING)).ok)
            response = client.request(WorkerRequest("asset", "worker-a", WorkerMessageType.HAS_ASSET, HasAssetRequest(_SHA)))
            self.assertTrue(response.payload.available)
            content = b"asset"
            self.assertTrue(client.request(WorkerRequest("put", "worker-a", WorkerMessageType.PUT_ASSET, PutAssetRequest(_SHA, len(content))), binary_payload=content).ok)
            self.assertEqual(self.handler.requests[-1][1], content)
        finally:
            client.close()

    def test_bad_local_payload_and_identity_handshake_failure(self) -> None:
        client = self._client()
        with self.assertRaises(EngineError):
            client.request(WorkerRequest("put", "worker-a", WorkerMessageType.PUT_ASSET, PutAssetRequest(_SHA, 2)), binary_payload=b"x")
        client.close()
        bad = self._client("worker-b")
        with self.assertRaises(EngineError):
            bad.connect()
        self.assertIsNone(bad._socket)

    def test_raw_non_hello_and_malformed_control_close_only_connection_then_server_recovers(self) -> None:
        raw = socket.create_connection((self.endpoint.host, self.endpoint.port), timeout=0.5)
        raw.sendall(encode_frame(self.codec.encode_request(WorkerRequest("x", "worker-a", WorkerMessageType.PING)), max_payload_bytes=MAX_CONTROL_FRAME_BYTES))
        self.assertEqual(raw.recv(1), b"")
        raw.close()
        raw = socket.create_connection((self.endpoint.host, self.endpoint.port), timeout=0.5)
        raw.sendall(b"\x00\x00")
        raw.close()
        client = self._client()
        try:
            client.connect()
            self.assertTrue(client.request(WorkerRequest("ping", "worker-a", WorkerMessageType.PING)).ok)
        finally:
            client.close()

    def test_non_loopback_bind_connect_refusal_and_disconnect_cleanup(self) -> None:
        with self.assertRaises(EngineError) as raised:
            TcpWorkerServer("worker-a", self.handler, self.codec, host="0.0.0.0")
        self.assertEqual(raised.exception.details["issue_code"], "NON_LOOPBACK_BIND_NOT_ENABLED")
        client = TcpWorkerClient("worker-a", WorkerEndpoint("127.0.0.1", 1), self.codec, WorkerProtocolValidator(), connect_timeout=0.1, handshake_timeout=0.1)
        with self.assertRaises(EngineError):
            client.connect()
        client = self._client()
        client.connect()
        client.close()
        client.close()
        for _ in range(20):
            if self.handler.shutdown_count:
                break
            self.stop.wait(0.02)
        self.assertGreaterEqual(self.handler.shutdown_count, 1)
