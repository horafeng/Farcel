from __future__ import annotations

import hashlib
from dataclasses import fields
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from farcel.application.remote_node_runtime import RemoteNodeRuntime
from farcel.application.remote_runtime_factory import RemoteNodeRuntimeFactory
from farcel.application.worker_client import WorkerRpcClient
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import SimulationConfig
from farcel.contracts.worker_protocol import (
    CreateRuntimeRequest,
    CreateRuntimeResponse,
    HasAssetResponse,
    PutAssetResponse,
    RemoteError,
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

    def request(self, request: WorkerRequest, *, binary_payload: bytes | None = None) -> WorkerResponse:
        self.requests.append(request)
        self.binary_payloads.append(binary_payload)
        outcome = self._responder(request, binary_payload)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self) -> None:
        self.close_count += 1


def _response(request: WorkerRequest, payload: object | None) -> WorkerResponse:
    return WorkerResponse(request.request_id, request.worker_id, request.message_type, True, payload)


class RemoteNodeRuntimeFactoryTests(unittest.TestCase):
    def _source(self, content: bytes = b"remote asset") -> tuple[TemporaryDirectory[str], Path, str]:
        directory = TemporaryDirectory()
        path = Path(directory.name) / "coordinator private model.fmu"
        path.write_bytes(content)
        return directory, path, hashlib.sha256(content).hexdigest()

    def _factory(self, transport: _Transport) -> RemoteNodeRuntimeFactory:
        ids = (f"request-{number}" for number in range(1, 100))
        return RemoteNodeRuntimeFactory(
            WorkerRpcClient("worker-a", transport, request_id_factory=lambda: next(ids))
        )

    def test_invalid_node_or_config_does_not_stage_or_connect(self) -> None:
        transport = _Transport(lambda request, _: _response(request, None))
        factory = self._factory(transport)
        with TemporaryDirectory() as directory:
            source = Path(directory) / "model.fmu"
            source.write_bytes(b"asset")
            for node_id, config in (("", SimulationConfig()), ("node", object())):
                with self.subTest(node_id=node_id, config=config):
                    with self.assertRaises(EngineError) as raised:
                        factory.create(node_id, source, config)  # type: ignore[arg-type]
                    self.assertIs(raised.exception.code, ErrorCode.VALIDATION_ERROR)
        self.assertEqual((transport.requests, transport.connect_count, transport.close_count), ([], 0, 0))

    def test_hit_creates_uninitialized_runtime_without_path_leakage(self) -> None:
        directory, path, sha256 = self._source()
        try:
            def responder(request: WorkerRequest, _: bytes | None) -> WorkerResponse:
                if request.message_type is WorkerMessageType.HAS_ASSET:
                    return _response(request, HasAssetResponse(sha256, True))
                if request.message_type is WorkerMessageType.CREATE_RUNTIME:
                    return _response(request, CreateRuntimeResponse("runtime-hit"))
                return _response(request, RuntimeAck("runtime-hit"))

            transport = _Transport(responder)
            runtime = self._factory(transport).create("node-a", path, SimulationConfig())
            self.assertIsInstance(runtime, RemoteNodeRuntime)
            self.assertEqual(
                [request.message_type for request in transport.requests],
                [WorkerMessageType.HAS_ASSET, WorkerMessageType.CREATE_RUNTIME],
            )
            create = transport.requests[-1].payload
            self.assertEqual(tuple(field.name for field in fields(CreateRuntimeRequest)), ("node_id", "asset_sha256", "config"))
            self.assertEqual((create.node_id, create.asset_sha256), ("node-a", sha256))
            self.assertFalse(any(str(path) in str(request.payload) for request in transport.requests))
            self.assertEqual((transport.connect_count, transport.close_count), (0, 0))

            runtime.initialize()
            self.assertEqual(transport.requests[-1].message_type, WorkerMessageType.INITIALIZE)
        finally:
            directory.cleanup()

    def test_miss_put_create_then_second_create_hits_same_asset(self) -> None:
        directory, path, sha256 = self._source()
        try:
            available = False
            runtime_ids = iter(("runtime-a", "runtime-b"))

            def responder(request: WorkerRequest, _: bytes | None) -> WorkerResponse:
                nonlocal available
                if request.message_type is WorkerMessageType.HAS_ASSET:
                    return _response(request, HasAssetResponse(sha256, available))
                if request.message_type is WorkerMessageType.PUT_ASSET:
                    available = True
                    return _response(request, PutAssetResponse(sha256))
                if request.message_type is WorkerMessageType.CREATE_RUNTIME:
                    return _response(request, CreateRuntimeResponse(next(runtime_ids)))
                return _response(request, RuntimeAck(request.payload.runtime_id))

            transport = _Transport(responder)
            factory = self._factory(transport)
            first = factory.create("node-a", path, SimulationConfig())
            second = factory.create("node-b", path, SimulationConfig())
            self.assertIsInstance(first, RemoteNodeRuntime)
            self.assertIsInstance(second, RemoteNodeRuntime)
            self.assertEqual(
                [request.message_type for request in transport.requests],
                [
                    WorkerMessageType.HAS_ASSET,
                    WorkerMessageType.PUT_ASSET,
                    WorkerMessageType.CREATE_RUNTIME,
                    WorkerMessageType.HAS_ASSET,
                    WorkerMessageType.CREATE_RUNTIME,
                ],
            )
            self.assertEqual(transport.binary_payloads[1], path.read_bytes())
            first.initialize()
            second.initialize()
            first.close()
            second.close()
            self.assertEqual(transport.close_count, 0)
        finally:
            directory.cleanup()

    def test_invalid_create_payload_and_blank_runtime_id_are_rejected(self) -> None:
        directory, path, sha256 = self._source()
        try:
            for payload in (RuntimeAck("runtime"), CreateRuntimeResponse("")):
                with self.subTest(payload=payload):
                    def responder(request: WorkerRequest, _: bytes | None) -> WorkerResponse:
                        if request.message_type is WorkerMessageType.HAS_ASSET:
                            return _response(request, HasAssetResponse(sha256, True))
                        return _response(request, payload)

                    with self.assertRaises(EngineError) as raised:
                        self._factory(_Transport(responder)).create("node", path, SimulationConfig())
                    self.assertIs(raised.exception.code, ErrorCode.INTERNAL_ERROR)
                    self.assertEqual(raised.exception.details["issue_code"], "UNEXPECTED_WORKER_PAYLOAD")
        finally:
            directory.cleanup()

    def test_create_business_error_and_transport_timeout_are_not_retried(self) -> None:
        directory, path, sha256 = self._source()
        try:
            for outcome in (
                RemoteError(ErrorCode.INSTANTIATION_ERROR, "create failed", {"node": "node"}),
                EngineError(ErrorCode.TIMEOUT, "create outcome unknown"),
            ):
                with self.subTest(outcome=outcome):
                    def responder(request: WorkerRequest, _: bytes | None) -> WorkerResponse | Exception:
                        if request.message_type is WorkerMessageType.HAS_ASSET:
                            return _response(request, HasAssetResponse(sha256, True))
                        if isinstance(outcome, RemoteError):
                            return WorkerResponse(
                                request.request_id,
                                request.worker_id,
                                request.message_type,
                                False,
                                error=outcome,
                            )
                        return outcome

                    transport = _Transport(responder)
                    with self.assertRaises(EngineError) as raised:
                        self._factory(transport).create("node", path, SimulationConfig())
                    self.assertEqual(raised.exception.code, outcome.code)
                    self.assertEqual(
                        [request.message_type for request in transport.requests],
                        [WorkerMessageType.HAS_ASSET, WorkerMessageType.CREATE_RUNTIME],
                    )
        finally:
            directory.cleanup()
