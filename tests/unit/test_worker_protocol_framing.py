from __future__ import annotations

import unittest

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.infrastructure.worker_protocol.framing import MAX_ASSET_FRAME_BYTES, MAX_CONTROL_FRAME_BYTES, decode_frame, encode_frame, parse_frame_header


class WorkerProtocolFramingTests(unittest.TestCase):
    def test_empty_json_and_binary_round_trip(self):
        for payload in (b"", b'{"x":1}', b"\x00\xff\x01"):
            with self.subTest(payload=payload): self.assertEqual(decode_frame(encode_frame(payload, max_payload_bytes=32), max_payload_bytes=32), payload)
    def test_header_is_four_byte_network_order(self):
        self.assertEqual(encode_frame(b"abc", max_payload_bytes=3)[:4], b"\x00\x00\x00\x03")
        self.assertEqual(parse_frame_header(b"\x00\x00\x00\x03", max_payload_bytes=3), 3)
    def test_malformed_limits_and_bodies_are_rejected(self):
        invalid_calls = [lambda: parse_frame_header(b"\x00\x00\x00", max_payload_bytes=1), lambda: parse_frame_header(b"\x00\x00\x00\x01\x00", max_payload_bytes=1), lambda: parse_frame_header(b"\x00\x00\x00\x02", max_payload_bytes=1), lambda: decode_frame(b"\x00\x00\x00\x02x", max_payload_bytes=2), lambda: decode_frame(b"\x00\x00\x00\x01xx", max_payload_bytes=2), lambda: encode_frame(b"xx", max_payload_bytes=1), lambda: encode_frame(bytearray(), max_payload_bytes=1), lambda: encode_frame(b"", max_payload_bytes=-1)]
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises(EngineError) as raised: call()
                self.assertEqual(raised.exception.code, ErrorCode.VALIDATION_ERROR)
    def test_declared_large_lengths_and_policy_constants(self):
        self.assertEqual(MAX_CONTROL_FRAME_BYTES, 1024 * 1024); self.assertEqual(MAX_ASSET_FRAME_BYTES, 512 * 1024 * 1024)
        with self.assertRaises(EngineError): parse_frame_header(b"\xff\xff\xff\xff", max_payload_bytes=MAX_CONTROL_FRAME_BYTES)
        self.assertEqual(parse_frame_header(b"\x00\x00\x00\x01", max_payload_bytes=MAX_ASSET_FRAME_BYTES), 1)
