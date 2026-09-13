from __future__ import annotations

import hashlib
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from farcel.application.graph_runtime_factory import GraphRuntimeBindingsFactory
from farcel.application.remote_runtime_factory import RemoteNodeRuntimeFactory
from farcel.application.worker_client import WorkerRpcClient
from farcel.application.worker_protocol import WorkerProtocolValidator
from farcel.contracts.distributed import ExecutionPlan, NodePlacement, PlacementKind
from farcel.contracts.graph import GraphSimulationConfig, ModelNode, ModelNodeConfig, SimulationGraph
from farcel.contracts.models import InterfaceType
from farcel.infrastructure.worker_process import LocalWorkerSubprocess
from farcel.infrastructure.worker_protocol.json_codec import JsonWorkerProtocolCodec
from farcel.infrastructure.worker_protocol.tcp_transport import TcpWorkerClient


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FMU_PATH = REPOSITORY_ROOT / "examples" / "fmus" / "VanDerPol.fmu"
_TIMEOUT_SECONDS = 10.0


class _RaisingImporter:
    def load(self, path):
        raise AssertionError(f"WORKER placement 不得调用 Coordinator importer: {path}")


class _RaisingRuntimeFactory:
    def create(self, metadata, config):
        raise AssertionError("WORKER placement 不得调用 Coordinator local runtime factory")


class GraphRemoteBindingIntegrationTests(unittest.TestCase):
    def test_worker_placement_binds_real_remote_vanderpol_runtime(self) -> None:
        self.assertTrue(FMU_PATH.is_file(), f"真实 VanDerPol FMU 不存在: {FMU_PATH}")
        content = FMU_PATH.read_bytes()
        asset_sha256 = hashlib.sha256(content).hexdigest()
        graph = SimulationGraph(
            nodes=(
                ModelNode(
                    "remote-vdp",
                    str(FMU_PATH),
                    ModelNodeConfig(
                        parameters={"mu": 2.0},
                        selected_outputs=("x0",),
                        execution_interface=InterfaceType.CO_SIMULATION,
                    ),
                ),
            ),
        )
        graph_config = GraphSimulationConfig(
            start_time=0.0,
            stop_time=0.02,
            communication_step=0.01,
        )

        with TemporaryDirectory() as temporary_directory:
            cache_root = Path(temporary_directory)
            launcher = LocalWorkerSubprocess(
                "worker-graph-binding",
                cache_root,
                startup_timeout=_TIMEOUT_SECONDS,
                shutdown_timeout=_TIMEOUT_SECONDS,
            )
            transport: TcpWorkerClient | None = None
            rpc: WorkerRpcClient | None = None
            runtime = None
            try:
                descriptor = launcher.start()
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
                rpc = WorkerRpcClient(descriptor.worker_id, transport)
                rpc.connect()
                plan = ExecutionPlan(
                    workers=(descriptor,),
                    placements=(
                        NodePlacement(
                            "remote-vdp",
                            PlacementKind.WORKER,
                            descriptor.worker_id,
                        ),
                    ),
                )
                bindings = GraphRuntimeBindingsFactory(
                    _RaisingImporter(),
                    _RaisingRuntimeFactory(),
                    _RaisingRuntimeFactory(),
                ).create_with_plan(
                    graph,
                    graph_config,
                    plan,
                    {descriptor.worker_id: RemoteNodeRuntimeFactory(rpc)},
                )

                self.assertEqual([node_id for node_id, _ in bindings], ["remote-vdp"])
                runtime = bindings[0][1]
                self.assertTrue(all(hasattr(runtime, name) for name in (
                    "initialize", "set_inputs", "advance_to", "read_outputs", "terminate", "close",
                )))
                cache_asset = cache_root / "assets" / f"{asset_sha256}.fmu"
                self.assertTrue(cache_asset.is_file())
                self.assertEqual(cache_asset.read_bytes(), content)

                runtime.initialize()
                initial = float(runtime.read_outputs()["x0"])
                self.assertTrue(math.isfinite(initial))
                self.assertAlmostEqual(initial, 2.0, places=8)
                runtime.advance_to(0.01)
                checkpoint = float(runtime.read_outputs()["x0"])
                self.assertTrue(math.isfinite(checkpoint))
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
