from __future__ import annotations

import hashlib
import math
import os
from dataclasses import fields
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from farcel import create_backend
from farcel.application.worker_protocol import WorkerProtocolValidator
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import InterfaceType, SimulationConfig
from farcel.contracts.worker_protocol import (
    AdvanceToRequest,
    CreateRuntimeRequest,
    CreateRuntimeResponse,
    HasAssetRequest,
    HasAssetResponse,
    PutAssetRequest,
    ReadOutputsResponse,
    RuntimeAck,
    RuntimeCommand,
    SetInputsRequest,
    WorkerMessageType,
    WorkerRequest,
    WorkerResponse,
)
from farcel.infrastructure.worker_process import LocalWorkerSubprocess
from farcel.infrastructure.worker_protocol.json_codec import JsonWorkerProtocolCodec
from farcel.infrastructure.worker_protocol.tcp_transport import TcpWorkerClient


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FMU_PATH = REPOSITORY_ROOT / "examples" / "fmus" / "VanDerPol.fmu"
_TIMEOUT_SECONDS = 10.0
_NUMERIC_REL_TOLERANCE = 1e-8
_NUMERIC_ABS_TOLERANCE = 1e-9


class _Requests:
    def __init__(self, worker_id: str, prefix: str) -> None:
        self._worker_id = worker_id
        self._prefix = prefix
        self._counter = 0

    def make(self, message_type: WorkerMessageType, payload: object | None = None) -> WorkerRequest:
        self._counter += 1
        return WorkerRequest(
            f"{self._prefix}-{self._counter}",
            self._worker_id,
            message_type,
            payload,
        )


class WorkerRealFmuIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not FMU_PATH.is_file():
            raise AssertionError(f"真实 VanDerPol FMU 不存在: {FMU_PATH}")
        cls.content = FMU_PATH.read_bytes()
        cls.sha256 = hashlib.sha256(cls.content).hexdigest()

    def _config(self, interface: InterfaceType) -> SimulationConfig:
        return SimulationConfig(
            start_time=0.0,
            stop_time=0.02,
            communication_step=0.01,
            parameters={"mu": 2.0},
            selected_outputs=("x0",),
            execution_interface=interface,
        )

    def _client(self, descriptor) -> TcpWorkerClient:
        validator = WorkerProtocolValidator()
        return TcpWorkerClient(
            descriptor.worker_id,
            descriptor.endpoint,
            JsonWorkerProtocolCodec(validator),
            validator,
            connect_timeout=_TIMEOUT_SECONDS,
            handshake_timeout=_TIMEOUT_SECONDS,
            operation_timeout=_TIMEOUT_SECONDS,
        )

    def _require_ok(self, response: WorkerResponse, context: str) -> object | None:
        if response.ok:
            return response.payload
        error = response.error
        self.fail(
            f"{context} failed: "
            f"{None if error is None else error.code.value} "
            f"{None if error is None else error.message} "
            f"{None if error is None else error.details}"
        )

    def _upload_asset(
        self,
        client: TcpWorkerClient,
        requests: _Requests,
        cache_root: Path,
    ) -> None:
        before = self._require_ok(
            client.request(
                requests.make(
                    WorkerMessageType.HAS_ASSET,
                    HasAssetRequest(self.sha256),
                )
            ),
            "HAS_ASSET before PUT_ASSET",
        )
        self.assertIsInstance(before, HasAssetResponse)
        self.assertFalse(before.available)

        uploaded = self._require_ok(
            client.request(
                requests.make(
                    WorkerMessageType.PUT_ASSET,
                    PutAssetRequest(self.sha256, len(self.content)),
                ),
                binary_payload=self.content,
            ),
            "PUT_ASSET",
        )
        self.assertEqual(uploaded.sha256, self.sha256)

        after = self._require_ok(
            client.request(
                requests.make(
                    WorkerMessageType.HAS_ASSET,
                    HasAssetRequest(self.sha256),
                )
            ),
            "HAS_ASSET after PUT_ASSET",
        )
        self.assertIsInstance(after, HasAssetResponse)
        self.assertTrue(after.available)

        cache_asset = cache_root / "assets" / f"{self.sha256}.fmu"
        self.assertTrue(cache_asset.is_file())
        self.assertEqual(cache_asset.read_bytes(), self.content)

    def _create_initialize_and_read(
        self,
        client: TcpWorkerClient,
        requests: _Requests,
        node_id: str,
        config: SimulationConfig,
    ) -> tuple[str, float]:
        self.assertEqual(
            tuple(field.name for field in fields(CreateRuntimeRequest)),
            ("node_id", "asset_sha256", "config"),
        )
        created = self._require_ok(
            client.request(
                requests.make(
                    WorkerMessageType.CREATE_RUNTIME,
                    CreateRuntimeRequest(node_id, self.sha256, config),
                )
            ),
            "CREATE_RUNTIME",
        )
        self.assertIsInstance(created, CreateRuntimeResponse)
        runtime_id = created.runtime_id
        self.assertTrue(runtime_id)
        self.assertNotIn(str(FMU_PATH), runtime_id)

        initialized = self._require_ok(
            client.request(
                requests.make(
                    WorkerMessageType.INITIALIZE,
                    RuntimeCommand(runtime_id),
                )
            ),
            "INITIALIZE",
        )
        self.assertIsInstance(initialized, RuntimeAck)
        self.assertEqual(initialized.runtime_id, runtime_id)
        return runtime_id, self._read_x0(client, requests, runtime_id, "initial READ_OUTPUTS")

    def _read_x0(
        self,
        client: TcpWorkerClient,
        requests: _Requests,
        runtime_id: str,
        context: str,
    ) -> float:
        response = self._require_ok(
            client.request(
                requests.make(WorkerMessageType.READ_OUTPUTS, RuntimeCommand(runtime_id))
            ),
            context,
        )
        self.assertIsInstance(response, ReadOutputsResponse)
        self.assertEqual(response.runtime_id, runtime_id)
        value = response.outputs["x0"]
        self.assertIsInstance(value, (int, float))
        self.assertNotIsInstance(value, bool)
        self.assertTrue(math.isfinite(float(value)))
        return float(value)

    def _advance_and_read(
        self,
        client: TcpWorkerClient,
        requests: _Requests,
        runtime_id: str,
        target_time: float,
    ) -> float:
        advanced = self._require_ok(
            client.request(
                requests.make(
                    WorkerMessageType.ADVANCE_TO,
                    AdvanceToRequest(runtime_id, target_time),
                )
            ),
            f"ADVANCE_TO {target_time}",
        )
        self.assertIsInstance(advanced, RuntimeAck)
        self.assertEqual(advanced.runtime_id, runtime_id)
        return self._read_x0(client, requests, runtime_id, f"READ_OUTPUTS after {target_time}")

    def _finish_runtime(self, client: TcpWorkerClient, requests: _Requests, runtime_id: str) -> None:
        terminated = self._require_ok(
            client.request(
                requests.make(WorkerMessageType.TERMINATE, RuntimeCommand(runtime_id))
            ),
            "TERMINATE",
        )
        self.assertIsInstance(terminated, RuntimeAck)
        self.assertEqual(terminated.runtime_id, runtime_id)

        closed = self._require_ok(
            client.request(
                requests.make(WorkerMessageType.CLOSE, RuntimeCommand(runtime_id))
            ),
            "CLOSE",
        )
        self.assertIsInstance(closed, RuntimeAck)
        self.assertEqual(closed.runtime_id, runtime_id)

        missing = client.request(
            requests.make(WorkerMessageType.READ_OUTPUTS, RuntimeCommand(runtime_id))
        )
        self.assertFalse(missing.ok)
        self.assertIsNotNone(missing.error)
        assert missing.error is not None
        self.assertIs(missing.error.code, ErrorCode.VALIDATION_ERROR)
        self.assertEqual(missing.error.details["issue_code"], "UNKNOWN_RUNTIME_ID")
        self.assertTrue(
            client.request(requests.make(WorkerMessageType.PING)).ok,
            "CLOSE 后 Worker TCP session 应保持可用",
        )

    def _assert_parity(self, local: float, remote: float) -> None:
        self.assertTrue(
            math.isclose(
                local,
                remote,
                rel_tol=_NUMERIC_REL_TOLERANCE,
                abs_tol=_NUMERIC_ABS_TOLERANCE,
            ),
            f"local={local!r}, remote={remote!r}",
        )

    def _run_full_lifecycle(self, interface: InterfaceType, worker_id: str, node_id: str) -> None:
        config = self._config(interface)
        local_result = create_backend().run_fmu(FMU_PATH, config)
        local_initial = float(local_result.outputs["x0"][0])
        local_final = float(local_result.outputs["x0"][-1])

        with TemporaryDirectory() as temporary_directory:
            cache_root = Path(temporary_directory)
            launcher = LocalWorkerSubprocess(
                worker_id,
                cache_root,
                startup_timeout=_TIMEOUT_SECONDS,
                shutdown_timeout=_TIMEOUT_SECONDS,
            )
            client: TcpWorkerClient | None = None
            try:
                descriptor = launcher.start()
                self.assertIsNotNone(launcher.pid)
                self.assertNotEqual(launcher.pid, os.getpid())
                client = self._client(descriptor)
                client.connect()
                requests = _Requests(worker_id, node_id)
                self._upload_asset(client, requests, cache_root)
                runtime_id, remote_initial = self._create_initialize_and_read(
                    client,
                    requests,
                    node_id,
                    config,
                )
                self._assert_parity(local_initial, remote_initial)

                set_inputs = self._require_ok(
                    client.request(
                        requests.make(
                            WorkerMessageType.SET_INPUTS,
                            SetInputsRequest(runtime_id, {}),
                        )
                    ),
                    "SET_INPUTS({})",
                )
                self.assertIsInstance(set_inputs, RuntimeAck)
                self.assertEqual(set_inputs.runtime_id, runtime_id)
                self._advance_and_read(client, requests, runtime_id, 0.01)
                remote_final = self._advance_and_read(client, requests, runtime_id, 0.02)
                self.assertFalse(
                    math.isclose(remote_initial, remote_final, rel_tol=0.0, abs_tol=1e-12),
                    "VanDerPol x0 应在真实 runtime 中实际推进",
                )
                self._assert_parity(local_final, remote_final)
                self._finish_runtime(client, requests, runtime_id)

                self.assertIsNone(launcher.poll(), "runtime CLOSE 不应终止 Worker process")
                client.close()
                client = None
                self.assertEqual(launcher.wait(_TIMEOUT_SECONDS), 0)
            finally:
                if client is not None:
                    client.close()
                launcher.close()

    def test_real_fmu_co_simulation_full_lifecycle_out_of_process(self) -> None:
        self._run_full_lifecycle(
            InterfaceType.CO_SIMULATION,
            "worker-real-cs",
            "vdp-cs",
        )

    def test_real_fmu_model_exchange_full_lifecycle_out_of_process(self) -> None:
        self._run_full_lifecycle(
            InterfaceType.MODEL_EXCHANGE,
            "worker-real-me",
            "vdp-me",
        )

    def test_parent_shutdown_cleans_active_real_model_exchange_runtime(self) -> None:
        config = self._config(InterfaceType.MODEL_EXCHANGE)
        with TemporaryDirectory() as temporary_directory:
            cache_root = Path(temporary_directory)
            launcher = LocalWorkerSubprocess(
                "worker-real-cleanup",
                cache_root,
                startup_timeout=_TIMEOUT_SECONDS,
                shutdown_timeout=_TIMEOUT_SECONDS,
            )
            client: TcpWorkerClient | None = None
            try:
                descriptor = launcher.start()
                client = self._client(descriptor)
                client.connect()
                requests = _Requests("worker-real-cleanup", "cleanup-me")
                self._upload_asset(client, requests, cache_root)
                runtime_id, _ = self._create_initialize_and_read(
                    client,
                    requests,
                    "vdp-cleanup-me",
                    config,
                )
                self._advance_and_read(client, requests, runtime_id, 0.01)

                launcher.close()
                self.assertEqual(launcher.wait(_TIMEOUT_SECONDS), 0)
                with self.assertRaises(EngineError):
                    client.request(requests.make(WorkerMessageType.PING))
                launcher.close()
            finally:
                if client is not None:
                    client.close()
                launcher.close()
