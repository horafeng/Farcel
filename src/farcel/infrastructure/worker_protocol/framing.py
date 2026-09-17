"""不含 socket I/O 的严格 length-prefixed bytes framing。"""

from __future__ import annotations

import struct

from farcel.contracts.errors import EngineError, ErrorCode


_HEADER = struct.Struct("!I")
MAX_CONTROL_FRAME_BYTES = 1 * 1024 * 1024
MAX_ASSET_FRAME_BYTES = 512 * 1024 * 1024


def encode_frame(payload: bytes, *, max_payload_bytes: int) -> bytes:
    try:
        _validate_limit(max_payload_bytes)
        if not isinstance(payload, bytes):
            raise ValueError("payload 必须是 bytes")
        if len(payload) > max_payload_bytes:
            raise ValueError("payload 超过允许大小")
        return _HEADER.pack(len(payload)) + payload
    except (TypeError, ValueError, struct.error) as exc:
        raise _error("worker_frame_encode", "Worker frame 无法编码", exc) from None


def parse_frame_header(header: bytes, *, max_payload_bytes: int) -> int:
    try:
        _validate_limit(max_payload_bytes)
        if not isinstance(header, bytes) or len(header) != _HEADER.size:
            raise ValueError("frame header 必须恰好包含 4 bytes")
        length = _HEADER.unpack(header)[0]
        if length > max_payload_bytes:
            raise ValueError("frame 声明长度超过允许大小")
        return length
    except (TypeError, ValueError, struct.error) as exc:
        raise _error("worker_frame_decode", "Worker frame header 无效", exc) from None


def decode_frame(frame: bytes, *, max_payload_bytes: int) -> bytes:
    try:
        if not isinstance(frame, bytes):
            raise ValueError("frame 必须是 bytes")
        if len(frame) < _HEADER.size:
            raise ValueError("frame 缺少完整 header")
        length = parse_frame_header(frame[:_HEADER.size], max_payload_bytes=max_payload_bytes)
        payload = frame[_HEADER.size:]
        if len(payload) != length:
            raise ValueError("frame body 长度与 header 不一致")
        return payload
    except EngineError:
        raise
    except (TypeError, ValueError) as exc:
        raise _error("worker_frame_decode", "Worker frame 无效", exc) from None


def _validate_limit(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("max_payload_bytes 必须是 uint32 范围内的整数")


def _error(phase: str, message: str, cause: Exception) -> EngineError:
    return EngineError(ErrorCode.VALIDATION_ERROR, message, {"phase": phase, "diagnostic": str(cause)})
