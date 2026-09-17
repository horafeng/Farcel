from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from farcel import create_backend
from farcel.application.graph_runner import GraphSimulationRunner
from farcel.application.graph_runtime_factory import GraphRuntimeBindingsFactory
from farcel.application.remote_runtime_factory import RemoteNodeRuntimeFactory
from farcel.application.worker_client import WorkerRpcClient
from farcel.application.worker_protocol import WorkerProtocolValidator
from farcel.contracts.distributed import ExecutionPlan, NodePlacement, PlacementKind
from farcel.contracts.graph import GraphSimulationConfig, ModelNode, ModelNodeConfig, SimulationGraph
from farcel.contracts.models import InterfaceType, SimulationState
from farcel.contracts.run_control import RunControl
from farcel.contracts.worker_protocol import WorkerMessageType
from farcel.infrastructure.worker_process import LocalWorkerSubprocess
from farcel.infrastructure.worker_protocol.json_codec import JsonWorkerProtocolCodec
from farcel.infrastructure.worker_protocol.tcp_transport import TcpWorkerClient


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FMU_PATH = REPOSITORY_ROOT / "examples" / "fmus" / "VanDerPol.fmu"
_TIMEOUT_SECONDS = 10.0
_REL_TOLERANCE = 1e-8
_ABS_TOLERANCE = 1e-9


class _RaisingImporter:
    def load(self, path):
        raise AssertionError(f"WORKER placement 不得调用 Coordinator importer: {path}")


class _RaisingRuntimeFactory:
    def create(self, metadata, config):
        raise AssertionError("WORKER placement 不得调用 Coordinator local runtime factory")


class RemoteGraphRunnerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(FMU_PATH.is_file(), f"真实 VanDerPol FMU 不存在: {FMU_PATH}")
        self.graph = SimulationGraph(
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
        self.config = GraphSimulationConfig(
            start_time=0.0,
            stop_time=0.02,
            communication_step=0.01,
        )

    def _run_remote_graph(self, *, control=None, on_progress=None):
        content = FMU_PATH.read_bytes()
        asset_sha256 = hashlib.sha256(content).hexdigest()
        with TemporaryDirectory() as temporary_directory:
            cache_root = Path(temporary_directory)
            launcher = LocalWorkerSubprocess(
                "worker-graph-runner",
                cache_root,
                startup_timeout=_TIMEOUT_SECONDS,
                shutdown_timeout=_TIMEOUT_SECONDS,
            )
            transport: TcpWorkerClient | None = None
            rpc: WorkerRpcClient | None = None
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
                rpc = WorkerRpcClient(descriptor.worker_id, transport)
                rpc.connect()
                execution_plan = ExecutionPlan(
                    workers=(descriptor,),
                    placements=(
                        NodePlacement(
                            node_id="remote-vdp",
                            kind=PlacementKind.WORKER,
                            worker_id=descriptor.worker_id,
                        ),
                    ),
                )
                remote_factory = RemoteNodeRuntimeFactory(rpc)
                graph_runtime_factory = GraphRuntimeBindingsFactory(
                    _RaisingImporter(),
                    _RaisingRuntimeFactory(),
                    _RaisingRuntimeFactory(),
                )
                runner = GraphSimulationRunner(
                    lambda: graph_runtime_factory.create_with_plan(
                        self.graph,
                        self.config,
                        execution_plan,
                        {descriptor.worker_id: remote_factory},
                    )
                )

                result = runner.run(
                    self.graph,
                    self.config,
                    control=control,
                    on_progress=on_progress,
                )
                cache_asset = cache_root / "assets" / f"{asset_sha256}.fmu"
                self.assertTrue(cache_asset.is_file())
                self.assertEqual(cache_asset.read_bytes(), content)

                self.assertIsNone(rpc.request(WorkerMessageType.PING))
                rpc.close()
                rpc = None
                self.assertEqual(launcher.wait(_TIMEOUT_SECONDS), 0)
                return result
            finally:
                if rpc is not None:
                    rpc.close()
                elif transport is not None:
                    transport.close()
                launcher.close()

    def test_remote_graph_runner_executes_real_worker_runtime_with_local_parity(self) -> None:
        backend = create_backend()
        self.assertTrue(backend.validate_graph(self.graph, self.config).is_valid)
        local_result = backend.run_graph(self.graph, self.config)

        remote_result = self._run_remote_graph()

        self.assertIs(local_result.completion_state, SimulationState.COMPLETED)
        self.assertIs(remote_result.completion_state, SimulationState.COMPLETED)
        self.assertEqual(remote_result.completed_steps, 2)
        self.assertEqual(remote_result.final_time, 0.02)
        self.assertEqual(remote_result.timestamps, (0.0, 0.01, 0.02))
        self.assertEqual(remote_result.timestamps, local_result.timestamps)
        self.assertEqual(remote_result.sample_count, local_result.sample_count)
        self.assertEqual(set(remote_result.node_outputs), {"remote-vdp"})
        self.assertEqual(set(remote_result.node_outputs["remote-vdp"]), {"x0"})
        local_samples = local_result.node_outputs["remote-vdp"]["x0"]
        remote_samples = remote_result.node_outputs["remote-vdp"]["x0"]
        self.assertEqual(len(remote_samples), 3)
        self.assertEqual(len(remote_samples), len(local_samples))
        for local, remote in zip(local_samples, remote_samples):
            self.assertIsInstance(remote, (int, float))
            self.assertTrue(math.isfinite(float(remote)))
            self.assertTrue(
                math.isclose(
                    float(local),
                    float(remote),
                    rel_tol=_REL_TOLERANCE,
                    abs_tol=_ABS_TOLERANCE,
                ),
                f"local={local!r}, remote={remote!r}",
            )
        self.assertAlmostEqual(float(remote_samples[0]), 2.0, places=8)
        self.assertFalse(math.isclose(float(remote_samples[0]), float(remote_samples[-1]), abs_tol=1e-12))

    def test_remote_graph_stop_after_initial_snapshot_keeps_connection_owned_outside_runner(self) -> None:
        control = RunControl()
        progress_events = []

        def on_progress(progress) -> None:
            progress_events.append(progress)
            if (
                progress.state is SimulationState.RUNNING
                and progress.current_time == self.config.start_time
            ):
                control.request_stop()

        result = self._run_remote_graph(control=control, on_progress=on_progress)

        self.assertIs(result.completion_state, SimulationState.STOPPED)
        self.assertEqual(result.completed_steps, 0)
        self.assertEqual(result.final_time, self.config.start_time)
        self.assertEqual(result.timestamps, (self.config.start_time,))
        self.assertEqual(result.sample_count, 1)
        samples = result.node_outputs["remote-vdp"]["x0"]
        self.assertEqual(len(samples), 1)
        self.assertAlmostEqual(float(samples[0]), 2.0, places=8)
        self.assertGreaterEqual(len(progress_events), 2)
        self.assertIs(progress_events[0].state, SimulationState.RUNNING)
        self.assertEqual(progress_events[0].current_time, self.config.start_time)
        self.assertIs(progress_events[-1].state, SimulationState.STOPPED)
        self.assertEqual(progress_events[-1].current_time, self.config.start_time)
        self.assertEqual(progress_events[-1].completed_steps, 0)
