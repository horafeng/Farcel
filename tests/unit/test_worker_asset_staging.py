from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from farcel.application.worker_asset_staging import WorkerAssetStager
from farcel.application.worker_client import WorkerRpcClient
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.worker_protocol import (
    HasAssetResponse,
    PutAssetResponse,
    RuntimeAck,
    WorkerMessageType,
    WorkerRequest,
    WorkerResponse,
)


class _Transport:
    def __init__(self, responder) -> None:
        self._responder = responder
        self.requests: list[WorkerRequest] = []
        self.binary_payloads: list[bytes | None] = []
        self.connect_count = 0
        self.close_count = 0

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


def _response(request: WorkerRequest, payload: object | None) -> WorkerResponse:
    return WorkerResponse(
        request.request_id,
        request.worker_id,
        request.message_type,
        True,
        payload,
    )


class WorkerAssetStagerTests(unittest.TestCase):
    def _stager(self, transport: _Transport) -> WorkerAssetStager:
        client = WorkerRpcClient("worker-a", transport, request_id_factory=lambda: "request")
        return WorkerAssetStager(client)

    def _source(self, content: bytes) -> tuple[TemporaryDirectory[str], Path, str]:
        directory = TemporaryDirectory()
        path = Path(directory.name) / "model.fmu"
        path.write_bytes(content)
        return directory, path, hashlib.sha256(content).hexdigest()

    def test_cache_hit_returns_streaming_sha_without_put(self) -> None:
        content = b"farcel-worker-asset"
        directory, path, sha256 = self._source(content)
        try:
            transport = _Transport(
                lambda request, _: _response(request, HasAssetResponse(sha256, True))
            )
            self.assertEqual(self._stager(transport).ensure_asset(path), sha256)
            self.assertEqual([request.message_type for request in transport.requests], [WorkerMessageType.HAS_ASSET])
            self.assertEqual(transport.binary_payloads, [None])
        finally:
            directory.cleanup()

    def test_cache_miss_uploads_exact_source_bytes_once(self) -> None:
        content = b"farcel-worker-asset"
        directory, path, sha256 = self._source(content)
        try:
            def responder(request: WorkerRequest, _: bytes | None) -> WorkerResponse:
                if request.message_type is WorkerMessageType.HAS_ASSET:
                    return _response(request, HasAssetResponse(sha256, False))
                return _response(request, PutAssetResponse(sha256))

            transport = _Transport(responder)
            self.assertEqual(self._stager(transport).ensure_asset(path), sha256)
            self.assertEqual(
                [request.message_type for request in transport.requests],
                [WorkerMessageType.HAS_ASSET, WorkerMessageType.PUT_ASSET],
            )
            put_request = transport.requests[1]
            self.assertEqual((put_request.payload.sha256, put_request.payload.size), (sha256, len(content)))
            self.assertEqual(transport.binary_payloads[1], content)
        finally:
            directory.cleanup()

    def test_invalid_source_does_not_send_requests(self) -> None:
        transport = _Transport(lambda request, _: _response(request, None))
        stager = self._stager(transport)
        with TemporaryDirectory() as directory:
            for source in (Path(directory) / "missing.fmu", Path(directory)):
                with self.subTest(source=source):
                    with self.assertRaises(EngineError) as raised:
                        stager.ensure_asset(source)
                    self.assertIs(raised.exception.code, ErrorCode.IMPORT_ERROR)
                    self.assertEqual(
                        raised.exception.details["issue_code"],
                        "REMOTE_ASSET_SOURCE_INVALID",
                    )
        self.assertEqual(transport.requests, [])

    def test_read_failure_is_import_error_without_put(self) -> None:
        directory, path, sha256 = self._source(b"asset")
        try:
            transport = _Transport(lambda request, _: _response(request, HasAssetResponse(sha256, False)))
            with patch.object(Path, "read_bytes", side_effect=OSError("read blocked")):
                with self.assertRaises(EngineError) as raised:
                    self._stager(transport).ensure_asset(path)
            self.assertIs(raised.exception.code, ErrorCode.IMPORT_ERROR)
            self.assertEqual(raised.exception.details["issue_code"], "REMOTE_ASSET_READ_FAILED")
            self.assertEqual([request.message_type for request in transport.requests], [WorkerMessageType.HAS_ASSET])
        finally:
            directory.cleanup()

    def test_source_mutation_between_hash_and_upload_is_rejected(self) -> None:
        original = b"first asset"
        changed = b"second asset"
        directory, path, sha256 = self._source(original)
        try:
            transport = _Transport(
                lambda request, _: _response(request, HasAssetResponse(sha256, False))
            )
            with patch.object(WorkerAssetStager, "_read_source", return_value=changed):
                with self.assertRaises(EngineError) as raised:
                    self._stager(transport).ensure_asset(path)
            self.assertIs(raised.exception.code, ErrorCode.IMPORT_ERROR)
            self.assertEqual(
                raised.exception.details["issue_code"],
                "REMOTE_ASSET_CHANGED_DURING_STAGING",
            )
            self.assertEqual([request.message_type for request in transport.requests], [WorkerMessageType.HAS_ASSET])
        finally:
            directory.cleanup()

    def test_unexpected_has_and_put_payloads_are_rejected(self) -> None:
        content = b"asset"
        directory, path, sha256 = self._source(content)
        try:
            has_cases = (None, RuntimeAck("runtime"), HasAssetResponse("b" * 64, True))
            for payload in has_cases:
                with self.subTest(has_payload=payload):
                    transport = _Transport(lambda request, _: _response(request, payload))
                    with self.assertRaises(EngineError) as raised:
                        self._stager(transport).ensure_asset(path)
                    self.assertIs(raised.exception.code, ErrorCode.INTERNAL_ERROR)
                    self.assertEqual(raised.exception.details["issue_code"], "UNEXPECTED_WORKER_PAYLOAD")

            transport = _Transport(
                lambda request, _: _response(
                    request,
                    HasAssetResponse(sha256, False)
                    if request.message_type is WorkerMessageType.HAS_ASSET
                    else RuntimeAck("runtime"),
                )
            )
            with self.assertRaises(EngineError) as raised:
                self._stager(transport).ensure_asset(path)
            self.assertIs(raised.exception.code, ErrorCode.INTERNAL_ERROR)
            self.assertEqual(raised.exception.details["issue_code"], "UNEXPECTED_WORKER_PAYLOAD")
        finally:
            directory.cleanup()

    def test_put_transport_ambiguity_is_not_replayed(self) -> None:
        content = b"asset"
        directory, path, sha256 = self._source(content)
        try:
            def responder(request: WorkerRequest, _: bytes | None) -> WorkerResponse | Exception:
                if request.message_type is WorkerMessageType.HAS_ASSET:
                    return _response(request, HasAssetResponse(sha256, False))
                return EngineError(ErrorCode.TIMEOUT, "PUT outcome unknown")

            transport = _Transport(responder)
            with self.assertRaises(EngineError) as raised:
                self._stager(transport).ensure_asset(path)
            self.assertIs(raised.exception.code, ErrorCode.TIMEOUT)
            self.assertEqual(
                [request.message_type for request in transport.requests],
                [WorkerMessageType.HAS_ASSET, WorkerMessageType.PUT_ASSET],
            )
        finally:
            directory.cleanup()
