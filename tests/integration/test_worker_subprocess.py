from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from farcel.application.worker_protocol import WorkerProtocolValidator
from farcel.contracts.errors import EngineError
from farcel.contracts.worker_protocol import (
    HasAssetRequest,
    PutAssetRequest,
    WorkerMessageType,
    WorkerRequest,
)
from farcel.infrastructure.worker_process import LocalWorkerSubprocess
from farcel.infrastructure.worker_protocol.json_codec import JsonWorkerProtocolCodec
from farcel.infrastructure.worker_protocol.tcp_transport import TcpWorkerClient


class WorkerSubprocessIntegrationTests(unittest.TestCase):
    def _client(self, descriptor) -> TcpWorkerClient:
        validator = WorkerProtocolValidator()
        return TcpWorkerClient(
            descriptor.worker_id,
            descriptor.endpoint,
            JsonWorkerProtocolCodec(validator),
            validator,
            connect_timeout=3,
            handshake_timeout=3,
            operation_timeout=3,
        )

    def _assert_readers_finished(self, launcher: LocalWorkerSubprocess) -> None:
        for thread in (launcher._stdout_thread, launcher._stderr_thread):
            self.assertIsNotNone(thread)
            self.assertFalse(thread.is_alive())

    def test_subprocess_asset_round_trip_natural_exit_and_cache_persistence(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            cache_root = Path(temporary_directory)
            first = LocalWorkerSubprocess("worker-a", cache_root, startup_timeout=5, shutdown_timeout=5)
            first_client: TcpWorkerClient | None = None
            try:
                descriptor = first.start()
                self.assertEqual(descriptor.worker_id, "worker-a")
                self.assertEqual(descriptor.endpoint.host, "127.0.0.1")
                self.assertGreater(descriptor.endpoint.port, 0)
                self.assertIsNotNone(first.pid)
                self.assertNotEqual(first.pid, os.getpid())
                first_pid = first.pid
                first_client = self._client(descriptor)
                first_client.connect()
                self.assertTrue(first_client.request(WorkerRequest("ping", "worker-a", WorkerMessageType.PING)).ok)
                content = b"worker subprocess asset"
                sha256 = hashlib.sha256(content).hexdigest()
                miss = first_client.request(WorkerRequest("has-before", "worker-a", WorkerMessageType.HAS_ASSET, HasAssetRequest(sha256)))
                self.assertFalse(miss.payload.available)
                self.assertTrue(first_client.request(WorkerRequest("put", "worker-a", WorkerMessageType.PUT_ASSET, PutAssetRequest(sha256, len(content))), binary_payload=content).ok)
                hit = first_client.request(WorkerRequest("has-after", "worker-a", WorkerMessageType.HAS_ASSET, HasAssetRequest(sha256)))
                self.assertTrue(hit.payload.available)
                asset_path = cache_root / "assets" / f"{sha256}.fmu"
                self.assertEqual(asset_path.read_bytes(), content)
                first_client.close()
                first_client = None
                self.assertEqual(first.wait(5), 0)
                self._assert_readers_finished(first)
                first.close()
                first.close()

                second = LocalWorkerSubprocess("worker-a", cache_root, startup_timeout=5, shutdown_timeout=5)
                second_client: TcpWorkerClient | None = None
                try:
                    second_descriptor = second.start()
                    self.assertNotEqual(second.pid, first_pid)
                    second_client = self._client(second_descriptor)
                    second_client.connect()
                    persisted = second_client.request(WorkerRequest("has-persisted", "worker-a", WorkerMessageType.HAS_ASSET, HasAssetRequest(sha256)))
                    self.assertTrue(persisted.payload.available)
                    second_client.close()
                    second_client = None
                    self.assertEqual(second.wait(5), 0)
                    self._assert_readers_finished(second)
                finally:
                    if second_client is not None:
                        second_client.close()
                    second.close()
            finally:
                if first_client is not None:
                    first_client.close()
                first.close()

    def test_no_client_close_is_deterministic_and_worker_module_help_is_packaged(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            launcher = LocalWorkerSubprocess("worker-a", Path(temporary_directory), startup_timeout=5, shutdown_timeout=5)
            try:
                launcher.start()
                self.assertIsNone(launcher.poll())
                launcher.close()
                self.assertEqual(launcher.wait(1), 0)
                self._assert_readers_finished(launcher)
                launcher.close()
            finally:
                launcher.close()

        with TemporaryDirectory() as outside_repository:
            result = subprocess.run(
                [sys.executable, "-m", "farcel.worker", "--help"],
                cwd=outside_repository,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=5,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--worker-id", result.stdout)

    def test_parent_close_with_active_client_is_graceful_and_closes_transport(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            launcher = LocalWorkerSubprocess("worker-a", Path(temporary_directory), startup_timeout=5, shutdown_timeout=5)
            client: TcpWorkerClient | None = None
            try:
                descriptor = launcher.start()
                client = self._client(descriptor)
                client.connect()
                self.assertTrue(client.request(WorkerRequest("ping", "worker-a", WorkerMessageType.PING)).ok)
                launcher.close()
                self.assertEqual(launcher.wait(1), 0)
                self._assert_readers_finished(launcher)
                with self.assertRaises(EngineError):
                    client.request(WorkerRequest("after-close", "worker-a", WorkerMessageType.PING))
                launcher.close()
            finally:
                if client is not None:
                    client.close()
                launcher.close()

    def test_subprocess_cache_root_with_spaces_uses_argv_safely(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            cache_root = Path(temporary_directory) / "cache root with spaces"
            launcher = LocalWorkerSubprocess("worker-a", cache_root, startup_timeout=5, shutdown_timeout=5)
            client: TcpWorkerClient | None = None
            try:
                descriptor = launcher.start()
                client = self._client(descriptor)
                client.connect()
                self.assertTrue(client.request(WorkerRequest("ping-spaces", "worker-a", WorkerMessageType.PING)).ok)
                launcher.close()
                self.assertEqual(launcher.wait(1), 0)
                self._assert_readers_finished(launcher)
            finally:
                if client is not None:
                    client.close()
                launcher.close()
