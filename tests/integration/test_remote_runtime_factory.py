from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from farcel.application.remote_runtime_factory import RemoteNodeRuntimeFactory
from farcel.application.worker_client import WorkerRpcClient
from farcel.application.worker_protocol import WorkerProtocolValidator
from farcel.contracts.models import InterfaceType, SimulationConfig
from farcel.infrastructure.worker_process import LocalWorkerSubprocess
from farcel.infrastructure.worker_protocol.json_codec import JsonWorkerProtocolCodec
from farcel.infrastructure.worker_protocol.tcp_transport import TcpWorkerClient


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FMU_PATH = REPOSITORY_ROOT / "examples" / "fmus" / "VanDerPol.fmu"
_TIMEOUT_SECONDS = 10.0


class RemoteRuntimeFactoryIntegrationTests(unittest.TestCase):
    def test_real_subprocess_stages_and_provisions_vanderpol_runtime(self) -> None:
        self.assertTrue(FMU_PATH.is_file(), f"真实 VanDerPol FMU 不存在: {FMU_PATH}")
        content = FMU_PATH.read_bytes()
        sha256 = hashlib.sha256(content).hexdigest()
        config = SimulationConfig(
            start_time=0.0,
            stop_time=0.02,
            communication_step=0.01,
            parameters={"mu": 2.0},
            selected_outputs=("x0",),
            execution_interface=InterfaceType.CO_SIMULATION,
        )
        with TemporaryDirectory() as temporary_directory:
            cache_root = Path(temporary_directory)
            launcher = LocalWorkerSubprocess(
                "worker-provisioning",
                cache_root,
                startup_timeout=_TIMEOUT_SECONDS,
                shutdown_timeout=_TIMEOUT_SECONDS,
            )
            transport: TcpWorkerClient | None = None
            rpc: WorkerRpcClient | None = None
            runtime = None
            try:
                descriptor = launcher.start()
                self.assertIsNotNone(launcher.pid)
                self.assertNotEqual(launcher.pid, os.getpid())
                validator = WorkerProtocolValidator()
                transport = TcpWorkerClient(
                    descriptor.worker_id,
                    descriptor.endpoint,
                    JsonWorkerProtocolCodec(validator),
                    validator,
                    connect_timeout=_TIMEOUT_SECONDS,
                    handshake_timeout=_TIMEOUT_SECONDS,
                    operation_timeout=_TIMEOUT_SECONDS,
                )
                rpc = WorkerRpcClient("worker-provisioning", transport)
                rpc.connect()

                runtime = RemoteNodeRuntimeFactory(rpc).create(
                    "remote-vdp",
                    FMU_PATH,
                    config,
                )
                cache_asset = cache_root / "assets" / f"{sha256}.fmu"
                self.assertTrue(cache_asset.is_file())
                self.assertEqual(cache_asset.read_bytes(), content)

                runtime.initialize()
                initial = runtime.read_outputs()["x0"]
                self.assertIsInstance(initial, (int, float))
                self.assertTrue(math.isfinite(float(initial)))
                self.assertAlmostEqual(float(initial), 2.0, places=8)
                runtime.advance_to(0.01)
                checkpoint = runtime.read_outputs()["x0"]
                self.assertIsInstance(checkpoint, (int, float))
                self.assertTrue(math.isfinite(float(checkpoint)))
                runtime.advance_to(0.02)
                advanced = runtime.read_outputs()["x0"]
                self.assertIsInstance(advanced, (int, float))
                self.assertTrue(math.isfinite(float(advanced)))
                self.assertFalse(math.isclose(float(initial), float(advanced), abs_tol=1e-12))
                runtime.terminate()
                runtime.close()
                runtime = None
                rpc.close()
                rpc = None
                self.assertEqual(launcher.wait(_TIMEOUT_SECONDS), 0)
            finally:
                if runtime is not None:
                    try:
                        runtime.terminate()
                    except Exception:
                        pass
                    try:
                        runtime.close()
                    except Exception:
                        pass
                if rpc is not None:
                    rpc.close()
                elif transport is not None:
                    transport.close()
                launcher.close()
