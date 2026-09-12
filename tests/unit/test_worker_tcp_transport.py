from __future__ import annotations

import json
import socket
import struct
from threading import Event, Thread
from unittest.mock import patch
import unittest

from farcel.application.worker_protocol import WorkerProtocolValidator
from farcel.application.worker_service import WorkerApplicationService
from farcel.contracts.distributed import WorkerEndpoint
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.worker_protocol import (
    AdvanceToRequest,
    HasAssetRequest,
    HasAssetResponse,
    PutAssetRequest,
    PutAssetResponse,
    RemoteError,
    WorkerMessageType,
    WorkerRequest,
    WorkerResponse,
)
from farcel.infrastructure.worker_protocol.framing import (
    MAX_ASSET_FRAME_BYTES,
    MAX_CONTROL_FRAME_BYTES,
    encode_frame,
    parse_frame_header,
)
from farcel.infrastructure.worker_protocol.json_codec import JsonWorkerProtocolCodec
from farcel.infrastructure.worker_protocol.tcp_transport import TcpWorkerClient, TcpWorkerServer


_SHA = "a" * 64


def _recv_exact(connection: socket.socket, size: int) -> bytes:
    received = bytearray()
    while len(received) < size:
        chunk = connection.recv(size - len(received))
        if not chunk:
            raise AssertionError("raw peer closed an incomplete frame")
        received.extend(chunk)
    return bytes(received)


def _recv_control(connection: socket.socket) -> bytes:
    header = _recv_exact(connection, 4)
    return _recv_exact(connection, parse_frame_header(header, max_payload_bytes=MAX_CONTROL_FRAME_BYTES))


class _Handler:
    def __init__(self, worker_id: str = "worker-a", *, fail_shutdown: bool = False) -> None:
        self.worker_id = worker_id
        self.requests: list[tuple[WorkerRequest, bytes | None]] = []
        self.shutdown_count = 0
        self.shutdown_event = Event()
        self.fail_shutdown = fail_shutdown
        self.block_ping = Event()
        self.release_ping = Event()
        self.fail_has_asset: RemoteError | None = None

    def handle_request(self, request: WorkerRequest, *, binary_payload: bytes | None = None) -> WorkerResponse:
        self.requests.append((request, binary_payload))
        if request.worker_id != self.worker_id:
            return WorkerResponse(request.request_id, self.worker_id, request.message_type, False, error=RemoteError(ErrorCode.VALIDATION_ERROR, "identity"))
        if request.message_type is WorkerMessageType.PING and self.block_ping.is_set():
            self.release_ping.wait(1)
        if request.message_type is WorkerMessageType.HAS_ASSET:
            if self.fail_has_asset is not None:
                return WorkerResponse(request.request_id, self.worker_id, request.message_type, False, error=self.fail_has_asset)
            return WorkerResponse(request.request_id, self.worker_id, request.message_type, True, HasAssetResponse(request.payload.sha256, True))
        if request.message_type is WorkerMessageType.PUT_ASSET:
            return WorkerResponse(request.request_id, self.worker_id, request.message_type, True, PutAssetResponse(request.payload.sha256))
        return WorkerResponse(request.request_id, self.worker_id, request.message_type, True)

    def shutdown(self) -> None:
        self.shutdown_count += 1
        self.shutdown_event.set()
        if self.fail_shutdown:
            raise RuntimeError("cleanup failure")


class _AssetStore:
    def __init__(self) -> None:
        self.content: dict[str, bytes] = {}

    def has_asset(self, sha256: str) -> bool:
        return sha256 in self.content

    def put_asset(self, sha256: str, content: bytes) -> None:
        self.content[sha256] = content

    def resolve_asset(self, sha256: str) -> object:
        raise AssertionError("runtime factory is not used by this transport test")


class WorkerTcpTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = WorkerProtocolValidator()
        self.codec = JsonWorkerProtocolCodec(self.validator)
        self.handler = _Handler()
        self.server, self.endpoint, self.stop, self.thread = self._start_server(self.handler)

    def tearDown(self) -> None:
        self._stop_server(self.server, self.stop, self.thread)

    def _start_server(self, handler: _Handler | WorkerApplicationService, *, handshake_timeout: float = 0.3, operation_timeout: float | None = 0.3) -> tuple[TcpWorkerServer, WorkerEndpoint, Event, Thread]:
        server = TcpWorkerServer("worker-a", handler, self.codec, handshake_timeout=handshake_timeout, operation_timeout=operation_timeout)
        endpoint = server.start()
        stop = Event()
        thread = Thread(target=server.serve_forever, kwargs={"stop_event": stop})
        thread.start()
        return server, endpoint, stop, thread

    def _stop_server(self, server: TcpWorkerServer, stop: Event, thread: Thread) -> None:
        stop.set()
        server.close()
        thread.join(2)
        self.assertFalse(thread.is_alive())

    def _client(self, worker_id: str = "worker-a", *, endpoint: WorkerEndpoint | None = None, connect_timeout: float = 0.3, handshake_timeout: float = 0.3, operation_timeout: float | None = 0.3) -> TcpWorkerClient:
        return TcpWorkerClient(worker_id, endpoint or self.endpoint, self.codec, self.validator, connect_timeout=connect_timeout, handshake_timeout=handshake_timeout, operation_timeout=operation_timeout, request_id_factory=lambda: "id")

    def _raw_hello(self) -> socket.socket:
        raw = socket.create_connection((self.endpoint.host, self.endpoint.port), timeout=0.3)
        raw.settimeout(0.5)
        request = WorkerRequest("raw-hello", "worker-a", WorkerMessageType.HELLO)
        raw.sendall(encode_frame(self.codec.encode_request(request), max_payload_bytes=MAX_CONTROL_FRAME_BYTES))
        self.assertTrue(self.codec.decode_response(_recv_control(raw)).ok)
        return raw

    def _assert_server_recovers(self) -> None:
        client = self._client()
        try:
            client.connect()
            self.assertTrue(client.request(WorkerRequest("recovery", "worker-a", WorkerMessageType.PING)).ok)
        finally:
            client.close()

    def _wait_for_shutdown_count(self, expected: int) -> None:
        for _ in range(50):
            if self.handler.shutdown_count >= expected:
                return
            Event().wait(0.01)
        self.fail("server did not finish connection cleanup")

    def test_timeout_parameters_require_positive_finite_values(self) -> None:
        invalid = (0, -1, True, False, float("nan"), float("inf"), float("-inf"), "1", None)
        for value in invalid:
            with self.subTest(server_handshake=value):
                with self.assertRaises(EngineError) as raised:
                    TcpWorkerServer("worker-a", self.handler, self.codec, handshake_timeout=value)
                self.assertEqual(raised.exception.code, ErrorCode.VALIDATION_ERROR)
            with self.subTest(client_connect=value):
                with self.assertRaises(EngineError) as raised:
                    self._client(connect_timeout=value)
                self.assertEqual(raised.exception.code, ErrorCode.VALIDATION_ERROR)
            with self.subTest(client_handshake=value):
                with self.assertRaises(EngineError) as raised:
                    self._client(handshake_timeout=value)
                self.assertEqual(raised.exception.code, ErrorCode.VALIDATION_ERROR)
        for value in (0, -1, True, False, float("nan"), float("inf"), float("-inf"), "1"):
            with self.subTest(server_operation=value):
                with self.assertRaises(EngineError) as raised:
                    TcpWorkerServer("worker-a", self.handler, self.codec, operation_timeout=value)
                self.assertEqual(raised.exception.code, ErrorCode.VALIDATION_ERROR)
            with self.subTest(client_operation=value):
                with self.assertRaises(EngineError) as raised:
                    self._client(operation_timeout=value)
                self.assertEqual(raised.exception.code, ErrorCode.VALIDATION_ERROR)
        TcpWorkerServer("worker-a", self.handler, self.codec, operation_timeout=None)
        self._client(operation_timeout=None)

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

    def test_connected_invalid_binary_payloads_are_rejected_before_send(self) -> None:
        client = self._client()
        try:
            client.connect()
            sent_count = len(self.handler.requests)
            with self.assertRaises(EngineError) as raised:
                client.request(WorkerRequest("put", "worker-a", WorkerMessageType.PUT_ASSET, PutAssetRequest(_SHA, 2)), binary_payload=b"x")
            self.assertEqual(raised.exception.code, ErrorCode.VALIDATION_ERROR)
            self.assertEqual(len(self.handler.requests), sent_count)
            with self.assertRaises(EngineError) as raised:
                client.request(WorkerRequest("binary", "worker-a", WorkerMessageType.PING), binary_payload=b"x")
            self.assertEqual(raised.exception.code, ErrorCode.VALIDATION_ERROR)
            self.assertEqual(len(self.handler.requests), sent_count)
            self.assertTrue(client.request(WorkerRequest("ping", "worker-a", WorkerMessageType.PING)).ok)
        finally:
            client.close()

    def test_business_failure_response_is_returned_without_transport_raise(self) -> None:
        expected = RemoteError(ErrorCode.IMPORT_ERROR, "asset unavailable", {"phase": "test"})
        self.handler.fail_has_asset = expected
        client = self._client()
        try:
            client.connect()
            response = client.request(WorkerRequest("asset", "worker-a", WorkerMessageType.HAS_ASSET, HasAssetRequest(_SHA)))
            self.assertFalse(response.ok)
            self.assertEqual(response.error, expected)
        finally:
            client.close()

    def test_client_rejects_second_hello_without_corrupting_connection(self) -> None:
        client = self._client()
        try:
            client.connect()
            sent_count = len(self.handler.requests)
            with self.assertRaises(EngineError) as raised:
                client.request(WorkerRequest("again", "worker-a", WorkerMessageType.HELLO))
            self.assertEqual(raised.exception.code, ErrorCode.VALIDATION_ERROR)
            self.assertEqual(len(self.handler.requests), sent_count)
            self.assertTrue(client.request(WorkerRequest("ping", "worker-a", WorkerMessageType.PING)).ok)
        finally:
            client.close()

    def test_handshake_identity_mismatch_closes_client(self) -> None:
        client = self._client("worker-b")
        with self.assertRaises(EngineError):
            client.connect()
        self.assertIsNone(client._socket)

    def test_connect_timeout_is_mapped_and_closes_client(self) -> None:
        client = self._client()
        with patch("farcel.infrastructure.worker_protocol.tcp_transport.socket.create_connection", side_effect=socket.timeout("test")):
            with self.assertRaises(EngineError) as raised:
                client.connect()
        self.assertEqual(raised.exception.code, ErrorCode.TIMEOUT)
        self.assertIsNone(client._socket)

    def test_handshake_timeout_is_mapped_and_closes_client(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        endpoint = WorkerEndpoint(*listener.getsockname())
        received = Event()

        def peer() -> None:
            connection, _ = listener.accept()
            try:
                _recv_control(connection)
                received.set()
                Event().wait(0.3)
            finally:
                connection.close()
                listener.close()

        thread = Thread(target=peer)
        thread.start()
        client = self._client(endpoint=endpoint, handshake_timeout=0.05)
        try:
            with self.assertRaises(EngineError) as raised:
                client.connect()
            self.assertEqual(raised.exception.code, ErrorCode.TIMEOUT)
            self.assertTrue(received.is_set())
            self.assertIsNone(client._socket)
        finally:
            thread.join(1)
            self.assertFalse(thread.is_alive())

    def test_operation_timeout_is_mapped_and_closes_client(self) -> None:
        self.handler.block_ping.set()
        client = self._client(operation_timeout=0.05)
        try:
            client.connect()
            with self.assertRaises(EngineError) as raised:
                client.request(WorkerRequest("slow", "worker-a", WorkerMessageType.PING))
            self.assertEqual(raised.exception.code, ErrorCode.TIMEOUT)
            self.assertIsNone(client._socket)
        finally:
            self.handler.release_ping.set()
            client.close()

    def test_response_correlation_mismatches_close_client_without_retry(self) -> None:
        for field in ("request_id", "worker_id", "message_type", "protocol_version"):
            with self.subTest(field=field):
                endpoint, thread = self._start_mismatching_peer(field)
                client = self._client(endpoint=endpoint)
                try:
                    client.connect()
                    with self.assertRaises(EngineError) as raised:
                        client.request(WorkerRequest("request", "worker-a", WorkerMessageType.PING))
                    self.assertEqual(raised.exception.details["issue_code"], "WORKER_RESPONSE_CORRELATION_FAILED")
                    self.assertIsNone(client._socket)
                finally:
                    client.close()
                    thread.join(1)
                    self.assertFalse(thread.is_alive())

    def _start_mismatching_peer(self, field: str) -> tuple[WorkerEndpoint, Thread]:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        endpoint = WorkerEndpoint(*listener.getsockname())

        def peer() -> None:
            connection, _ = listener.accept()
            try:
                hello = self.codec.decode_request(_recv_control(connection))
                hello_response = WorkerResponse(hello.request_id, "worker-a", WorkerMessageType.HELLO, True)
                connection.sendall(encode_frame(self.codec.encode_response(hello_response), max_payload_bytes=MAX_CONTROL_FRAME_BYTES))
                request = self.codec.decode_request(_recv_control(connection))
                response = {"protocol_version": "1.0", "request_id": request.request_id, "worker_id": "worker-a", "message_type": request.message_type.value, "ok": True, "payload": None, "error": None}
                if field == "request_id":
                    response[field] = "different"
                elif field == "worker_id":
                    response[field] = "worker-b"
                elif field == "message_type":
                    response[field] = WorkerMessageType.HAS_ASSET.value
                    response["payload"] = {"sha256": _SHA, "available": True}
                else:
                    response[field] = "2.0"
                encoded = json.dumps(response, separators=(",", ":"), sort_keys=True).encode("utf-8")
                connection.sendall(encode_frame(encoded, max_payload_bytes=MAX_CONTROL_FRAME_BYTES))
            finally:
                connection.close()
                listener.close()

        thread = Thread(target=peer)
        thread.start()
        return endpoint, thread

    def test_no_replay_after_response_loss_executes_stateful_request_once(self) -> None:
        executions = [0]
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        endpoint = WorkerEndpoint(*listener.getsockname())

        def peer() -> None:
            connection, _ = listener.accept()
            try:
                hello = self.codec.decode_request(_recv_control(connection))
                response = WorkerResponse(hello.request_id, "worker-a", WorkerMessageType.HELLO, True)
                connection.sendall(encode_frame(self.codec.encode_response(response), max_payload_bytes=MAX_CONTROL_FRAME_BYTES))
                request = self.codec.decode_request(_recv_control(connection))
                self.assertIs(request.message_type, WorkerMessageType.ADVANCE_TO)
                executions[0] += 1
            finally:
                connection.close()
                listener.close()

        thread = Thread(target=peer)
        thread.start()
        client = self._client(endpoint=endpoint)
        try:
            client.connect()
            with self.assertRaises(EngineError):
                client.request(WorkerRequest("advance", "worker-a", WorkerMessageType.ADVANCE_TO, AdvanceToRequest("runtime", 1.0)))
            self.assertEqual(executions[0], 1)
            self.assertIsNone(client._socket)
        finally:
            client.close()
            thread.join(1)
            self.assertFalse(thread.is_alive())

    def test_oversized_control_header_partial_and_truncated_bodies_close_only_current_connection(self) -> None:
        expected_shutdown = self.handler.shutdown_count + 1
        raw = socket.create_connection((self.endpoint.host, self.endpoint.port), timeout=0.3)
        raw.sendall(struct.pack("!I", MAX_CONTROL_FRAME_BYTES + 1))
        self.assertEqual(raw.recv(1), b"")
        raw.close()
        self._wait_for_shutdown_count(expected_shutdown)
        self._assert_server_recovers()
        expected_shutdown = self.handler.shutdown_count + 1
        raw = socket.create_connection((self.endpoint.host, self.endpoint.port), timeout=0.3)
        raw.sendall(b"\x00\x00")
        raw.close()
        self._wait_for_shutdown_count(expected_shutdown)
        self._assert_server_recovers()
        expected_shutdown = self.handler.shutdown_count + 1
        raw = socket.create_connection((self.endpoint.host, self.endpoint.port), timeout=0.3)
        raw.sendall(struct.pack("!I", 100) + b"short")
        raw.close()
        self._wait_for_shutdown_count(expected_shutdown)
        self._assert_server_recovers()

    def test_invalid_utf8_and_malformed_json_close_only_current_connection(self) -> None:
        for body in (b"\xff", b"{"):
            with self.subTest(body=body):
                expected_shutdown = self.handler.shutdown_count + 1
                raw = socket.create_connection((self.endpoint.host, self.endpoint.port), timeout=0.3)
                raw.sendall(struct.pack("!I", len(body)) + body)
                self.assertEqual(raw.recv(1), b"")
                raw.close()
                self._wait_for_shutdown_count(expected_shutdown)
                self._assert_server_recovers()

    def test_put_asset_sidecar_length_failures_are_rejected_before_body(self) -> None:
        for header_size in (2, MAX_ASSET_FRAME_BYTES + 1):
            with self.subTest(header_size=header_size):
                expected_shutdown = self.handler.shutdown_count + 1
                raw = self._raw_hello()
                request = WorkerRequest("put", "worker-a", WorkerMessageType.PUT_ASSET, PutAssetRequest(_SHA, 3))
                raw.sendall(encode_frame(self.codec.encode_request(request), max_payload_bytes=MAX_CONTROL_FRAME_BYTES))
                raw.sendall(struct.pack("!I", header_size))
                self.assertEqual(raw.recv(1), b"")
                raw.close()
                self._wait_for_shutdown_count(expected_shutdown)
                self.assertFalse(any(item[0].request_id == "put" for item in self.handler.requests))
                self._assert_server_recovers()

    def test_truncated_asset_body_is_not_dispatched_and_server_recovers(self) -> None:
        expected_shutdown = self.handler.shutdown_count + 1
        raw = self._raw_hello()
        request = WorkerRequest("truncated-put", "worker-a", WorkerMessageType.PUT_ASSET, PutAssetRequest(_SHA, 4))
        raw.sendall(encode_frame(self.codec.encode_request(request), max_payload_bytes=MAX_CONTROL_FRAME_BYTES))
        raw.sendall(struct.pack("!I", 4) + b"x")
        raw.close()
        self._wait_for_shutdown_count(expected_shutdown)
        self.assertFalse(any(item[0].request_id == "truncated-put" for item in self.handler.requests))
        self._assert_server_recovers()

    def test_disconnect_shutdown_failure_recovery_and_second_client_reconnect(self) -> None:
        self._stop_server(self.server, self.stop, self.thread)
        failing = _Handler(fail_shutdown=True)
        self.server, self.endpoint, self.stop, self.thread = self._start_server(failing)
        first = self._client()
        try:
            first.connect()
            self.assertTrue(first.request(WorkerRequest("first", "worker-a", WorkerMessageType.PING)).ok)
        finally:
            first.close()
        self.assertTrue(failing.shutdown_event.wait(0.5))
        second = self._client()
        try:
            second.connect()
            self.assertTrue(second.request(WorkerRequest("second", "worker-a", WorkerMessageType.PING)).ok)
        finally:
            second.close()

    def test_real_worker_application_service_round_trip(self) -> None:
        self._stop_server(self.server, self.stop, self.thread)
        store = _AssetStore()
        service = WorkerApplicationService("worker-a", store, object())
        self.server, self.endpoint, self.stop, self.thread = self._start_server(service)
        client = self._client()
        try:
            client.connect()
            self.assertTrue(client.request(WorkerRequest("ping", "worker-a", WorkerMessageType.PING)).ok)
            miss = client.request(WorkerRequest("has-before", "worker-a", WorkerMessageType.HAS_ASSET, HasAssetRequest(_SHA)))
            self.assertFalse(miss.payload.available)
            content = b"worker-asset"
            self.assertTrue(client.request(WorkerRequest("put", "worker-a", WorkerMessageType.PUT_ASSET, PutAssetRequest(_SHA, len(content))), binary_payload=content).ok)
            hit = client.request(WorkerRequest("has-after", "worker-a", WorkerMessageType.HAS_ASSET, HasAssetRequest(_SHA)))
            self.assertTrue(hit.payload.available)
            self.assertEqual(store.content[_SHA], content)
        finally:
            client.close()

    def test_non_loopback_bind_and_refused_connection_are_stable_errors(self) -> None:
        with self.assertRaises(EngineError) as raised:
            TcpWorkerServer("worker-a", self.handler, self.codec, host="0.0.0.0")
        self.assertEqual(raised.exception.details["issue_code"], "NON_LOOPBACK_BIND_NOT_ENABLED")
        client = self._client()
        with patch("farcel.infrastructure.worker_protocol.tcp_transport.socket.create_connection", side_effect=ConnectionRefusedError("refused")):
            with self.assertRaises(EngineError) as raised:
                client.connect()
        self.assertEqual(raised.exception.code, ErrorCode.INTERNAL_ERROR)
