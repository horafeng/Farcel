"""Phase 7 v1 的 localhost TCP Worker request/response transport。"""

from __future__ import annotations

import math
import socket
from threading import Event
from typing import Callable
from uuid import uuid4

from farcel.contracts.distributed import WorkerEndpoint
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.ports import (
    WorkerProtocolSemanticValidator,
    WorkerRequestHandler,
)
from farcel.contracts.worker_protocol import (
    PutAssetRequest,
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


_HEADER_SIZE = 4


class TcpWorkerServer:
    """仅限 IPv4 loopback 的顺序 Worker TCP server。"""

    def __init__(
        self,
        worker_id: str,
        handler: WorkerRequestHandler,
        codec: JsonWorkerProtocolCodec,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        handshake_timeout: float = 5.0,
        operation_timeout: float | None = None,
    ) -> None:
        if host != "127.0.0.1":
            raise EngineError(
                ErrorCode.VALIDATION_ERROR,
                "Phase 7.2C 仅允许绑定 127.0.0.1",
                {"phase": "worker_tcp_bind", "issue_code": "NON_LOOPBACK_BIND_NOT_ENABLED"},
            )
        self._worker_id = worker_id
        self._handler = handler
        self._codec = codec
        self._host = host
        self._port = port
        self._handshake_timeout = _finite_timeout(handshake_timeout, "worker_tcp_handshake")
        self._operation_timeout = _optional_timeout(operation_timeout, "worker_tcp_operation")
        self._listener: socket.socket | None = None
        self._active: socket.socket | None = None

    def start(self) -> WorkerEndpoint:
        if self._listener is not None:
            host, port = self._listener.getsockname()
            return WorkerEndpoint(host, port)
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self._host, self._port))
            listener.listen(1)
            listener.settimeout(0.1)
            self._listener = listener
            host, port = listener.getsockname()
            return WorkerEndpoint(host, port)
        except OSError as exc:
            raise _transport_error("worker_tcp_bind", "WORKER_CONNECT_FAILED", "Worker listener 无法启动", exc) from None

    def serve_forever(self, *, stop_event: Event | None = None) -> None:
        listener = self._listener or self.start() and self._listener
        assert listener is not None
        while stop_event is None or not stop_event.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._listener is None:
                    return
                continue
            self._active = connection
            try:
                self._serve_connection(connection)
            finally:
                self._active = None
                _close_socket(connection)

    def close(self) -> None:
        listener, self._listener = self._listener, None
        active, self._active = self._active, None
        _close_socket(active)
        _close_socket(listener)

    def _serve_connection(self, connection: socket.socket) -> None:
        try:
            connection.settimeout(self._handshake_timeout)
            request = self._receive_request(connection)
            if request is None or request.message_type is not WorkerMessageType.HELLO:
                return
            response = self._handler.handle_request(request)
            self._send_response(connection, response)
            if not response.ok:
                return
            connection.settimeout(self._operation_timeout)
            while True:
                request = self._receive_request(connection)
                if request is None:
                    return
                binary_payload = self._receive_asset_sidecar(connection, request)
                response = self._handler.handle_request(request, binary_payload=binary_payload)
                self._send_response(connection, response)
        except (EngineError, OSError, Exception):
            return
        finally:
            try:
                self._handler.shutdown()
            except Exception:
                pass

    def _receive_request(self, connection: socket.socket) -> WorkerRequest | None:
        payload = _receive_frame(connection, MAX_CONTROL_FRAME_BYTES, "worker_tcp_control", allow_eof=True)
        if payload is None:
            return None
        return self._codec.decode_request(payload)

    def _receive_asset_sidecar(self, connection: socket.socket, request: WorkerRequest) -> bytes | None:
        if request.message_type is not WorkerMessageType.PUT_ASSET:
            return None
        assert isinstance(request.payload, PutAssetRequest)
        header = _recv_exact(connection, _HEADER_SIZE, phase="worker_tcp_asset_header")
        length = parse_frame_header(header, max_payload_bytes=MAX_ASSET_FRAME_BYTES)
        if length != request.payload.size:
            raise EngineError(
                ErrorCode.VALIDATION_ERROR,
                "PUT_ASSET binary frame 长度与声明不一致",
                {"phase": "worker_tcp_asset", "issue_code": "WORKER_FRAME_TRUNCATED"},
            )
        return _recv_exact(connection, length, phase="worker_tcp_asset_body")

    def _send_response(self, connection: socket.socket, response: WorkerResponse) -> None:
        _send_all(connection, encode_frame(self._codec.encode_response(response), max_payload_bytes=MAX_CONTROL_FRAME_BYTES), "worker_tcp_send")


class TcpWorkerClient:
    """持久 localhost TCP connection 上的无重试 Worker client。"""

    def __init__(
        self,
        worker_id: str,
        endpoint: WorkerEndpoint,
        codec: JsonWorkerProtocolCodec,
        validator: WorkerProtocolSemanticValidator,
        *,
        connect_timeout: float = 5.0,
        handshake_timeout: float = 5.0,
        operation_timeout: float | None = None,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._worker_id = worker_id
        self._endpoint = endpoint
        self._codec = codec
        self._validator = validator
        self._connect_timeout = _finite_timeout(connect_timeout, "worker_tcp_connect")
        self._handshake_timeout = _finite_timeout(handshake_timeout, "worker_tcp_handshake")
        self._operation_timeout = _optional_timeout(operation_timeout, "worker_tcp_operation")
        self._request_id_factory = request_id_factory or (lambda: uuid4().hex)
        self._socket: socket.socket | None = None

    def connect(self) -> None:
        if self._socket is not None:
            return
        try:
            connection = socket.create_connection((self._endpoint.host, self._endpoint.port), timeout=self._connect_timeout)
            self._socket = connection
            connection.settimeout(self._handshake_timeout)
            request = WorkerRequest(self._request_id_factory(), self._worker_id, WorkerMessageType.HELLO)
            response = self._round_trip(request, None)
            if not response.ok:
                raise EngineError(ErrorCode.VALIDATION_ERROR, "Worker HELLO 被拒绝", {"phase": "worker_handshake"})
            connection.settimeout(self._operation_timeout)
        except EngineError:
            self.close()
            raise
        except (socket.timeout, TimeoutError) as exc:
            self.close()
            raise _timeout_error("worker_tcp_connect", exc) from None
        except OSError as exc:
            self.close()
            raise _transport_error("worker_tcp_connect", "WORKER_CONNECT_FAILED", "无法连接 Worker", exc) from None

    def request(self, request: WorkerRequest, *, binary_payload: bytes | None = None) -> WorkerResponse:
        if self._socket is None:
            raise EngineError(ErrorCode.INTERNAL_ERROR, "Worker client 尚未连接", {"phase": "worker_tcp_request"})
        if request.message_type is WorkerMessageType.HELLO or request.worker_id != self._worker_id:
            raise EngineError(ErrorCode.VALIDATION_ERROR, "Worker request identity 或 message_type 无效", {"phase": "worker_tcp_request"})
        if request.message_type is WorkerMessageType.PUT_ASSET:
            if not isinstance(binary_payload, bytes) or not isinstance(request.payload, PutAssetRequest) or len(binary_payload) != request.payload.size:
                raise EngineError(ErrorCode.VALIDATION_ERROR, "PUT_ASSET binary payload 无效", {"phase": "worker_asset_upload"})
        elif binary_payload is not None:
            raise EngineError(ErrorCode.VALIDATION_ERROR, "非 PUT_ASSET 不允许 binary payload", {"phase": "worker_request_binary_payload"})
        try:
            return self._round_trip(request, binary_payload)
        except EngineError:
            self.close()
            raise
        except (socket.timeout, TimeoutError) as exc:
            self.close()
            raise _timeout_error("worker_tcp_operation", exc) from None
        except OSError as exc:
            self.close()
            raise _transport_error("worker_tcp_operation", "WORKER_DISCONNECTED", "Worker connection 中断", exc) from None

    def close(self) -> None:
        connection, self._socket = self._socket, None
        _close_socket(connection)

    def _round_trip(self, request: WorkerRequest, binary_payload: bytes | None) -> WorkerResponse:
        assert self._socket is not None
        _send_all(self._socket, encode_frame(self._codec.encode_request(request), max_payload_bytes=MAX_CONTROL_FRAME_BYTES), "worker_tcp_send")
        if binary_payload is not None:
            _send_all(self._socket, encode_frame(binary_payload, max_payload_bytes=MAX_ASSET_FRAME_BYTES), "worker_tcp_asset_send")
        payload = _receive_frame(self._socket, MAX_CONTROL_FRAME_BYTES, "worker_tcp_response", allow_eof=False)
        assert payload is not None
        try:
            response = self._codec.decode_response(payload)
        except EngineError:
            raise _correlation_error() from None
        report = self._validator.validate_response(response, request=request)
        if not report.is_valid:
            raise _correlation_error()
        return response


def _receive_frame(socket_object: socket.socket, limit: int, phase: str, *, allow_eof: bool) -> bytes | None:
    header = _recv_exact(socket_object, _HEADER_SIZE, phase=phase, allow_eof=allow_eof)
    if header is None:
        return None
    length = parse_frame_header(header, max_payload_bytes=limit)
    return _recv_exact(socket_object, length, phase=phase)


def _recv_exact(socket_object: socket.socket, size: int, *, phase: str, allow_eof: bool = False) -> bytes | None:
    received = bytearray()
    try:
        while len(received) < size:
            chunk = socket_object.recv(size - len(received))
            if not chunk:
                if not received and allow_eof:
                    return None
                raise EngineError(ErrorCode.INTERNAL_ERROR, "Worker frame 被截断", {"phase": phase, "issue_code": "WORKER_FRAME_TRUNCATED"})
            received.extend(chunk)
    except EngineError:
        raise
    except (socket.timeout, TimeoutError) as exc:
        raise _timeout_error(phase, exc) from None
    except OSError as exc:
        raise _transport_error(phase, "WORKER_DISCONNECTED", "Worker connection 中断", exc) from None
    return bytes(received)


def _send_all(socket_object: socket.socket, data: bytes, phase: str) -> None:
    try:
        socket_object.sendall(data)
    except (socket.timeout, TimeoutError) as exc:
        raise _timeout_error(phase, exc) from None
    except OSError as exc:
        raise _transport_error(phase, "WORKER_DISCONNECTED", "Worker connection 中断", exc) from None


def _finite_timeout(value: float, phase: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise EngineError(
            ErrorCode.VALIDATION_ERROR,
            "timeout 必须是正的有限数",
            {"phase": phase},
        )
    return float(value)


def _optional_timeout(value: float | None, phase: str) -> float | None:
    return None if value is None else _finite_timeout(value, phase)


def _timeout_error(phase: str, cause: Exception) -> EngineError:
    return EngineError(ErrorCode.TIMEOUT, "Worker transport 超时", {"phase": phase, "diagnostic": str(cause)})


def _correlation_error() -> EngineError:
    return EngineError(
        ErrorCode.INTERNAL_ERROR,
        "Worker response correlation 无效",
        {"phase": "worker_tcp_response", "issue_code": "WORKER_RESPONSE_CORRELATION_FAILED"},
    )


def _transport_error(phase: str, issue_code: str, message: str, cause: Exception) -> EngineError:
    return EngineError(ErrorCode.INTERNAL_ERROR, message, {"phase": phase, "issue_code": issue_code, "diagnostic": str(cause)})


def _close_socket(socket_object: socket.socket | None) -> None:
    if socket_object is None:
        return
    try:
        socket_object.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        socket_object.close()
    except OSError:
        pass
