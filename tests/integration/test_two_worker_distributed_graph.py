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
from farcel.contracts.graph import (
    Connection,
    GraphSimulationConfig,
    ModelNode,
    ModelNodeConfig,
    PortReference,
    SimulationGraph,
)
from farcel.contracts.models import InterfaceType, SimulationState
from farcel.contracts.worker_protocol import (
    CreateRuntimeRequest,
    ReadOutputsResponse,
    SetInputsRequest,
    WorkerMessageType,
)
from farcel.infrastructure.worker_process import LocalWorkerSubprocess
from farcel.infrastructure.worker_protocol.json_codec import JsonWorkerProtocolCodec
from farcel.infrastructure.worker_protocol.tcp_transport import TcpWorkerClient


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FMU_PATH = REPOSITORY_ROOT / "examples" / "fmus" / "Feedthrough-fmi2.fmu"
INPUT = "Float64_continuous_input"
OUTPUT = "Float64_continuous_output"
_TIMEOUT_SECONDS = 10.0
_REL_TOLERANCE = 1e-9
_ABS_TOLERANCE = 1e-10


class _RecordingTransport:
    """Transparent real-TCP wrapper that records one Worker's exchanges."""

    def __init__(self, delegate) -> None:
        self._delegate = delegate
        self.exchanges = []

    def connect(self) -> None:
        self._delegate.connect()

    def request(self, request, *, binary_payload=None):
        response = self._delegate.request(request, binary_payload=binary_payload)
        self.exchanges.append((request, response, binary_payload))
        return response

    def close(self) -> None:
        self._delegate.close()


class _RaisingImporter:
    def load(self, path):
        raise AssertionError(f"two-worker binding 不得加载 Coordinator 本地 FMU: {path}")


class _RaisingRuntimeFactory:
    def create(self, *args, **kwargs):
        raise AssertionError("two-worker binding 不得创建 Coordinator 本地 runtime")


class TwoWorkerDistributedGraphIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(FMU_PATH.is_file(), f"真实 Feedthrough FMU 不存在: {FMU_PATH}")
        self.config = GraphSimulationConfig(
            start_time=0.0,
            stop_time=0.02,
            communication_step=0.01,
        )

    def _graph(self, node_order: tuple[str, str]) -> SimulationGraph:
        nodes = {
            "A": ModelNode(
                "A",
                str(FMU_PATH),
                ModelNodeConfig(
                    initial_inputs={INPUT: 2.0},
                    execution_interface=InterfaceType.CO_SIMULATION,
                ),
            ),
            "B": ModelNode(
                "B",
                str(FMU_PATH),
                ModelNodeConfig(
                    selected_outputs=(OUTPUT,),
                    execution_interface=InterfaceType.CO_SIMULATION,
                ),
            ),
        }
        return SimulationGraph(
            nodes=tuple(nodes[node_id] for node_id in node_order),
            connections=(
                Connection(
                    PortReference("A", OUTPUT),
                    PortReference("B", INPUT),
                ),
            ),
        )

    def _local_result(self, graph: SimulationGraph):
        backend = create_backend()
        self.assertTrue(backend.validate_graph(graph, self.config).is_valid)
        result = backend.run_graph(graph, self.config)
        self.assertIs(result.completion_state, SimulationState.COMPLETED)
        self.assertEqual(result.completed_steps, 2)
        self.assertEqual(result.timestamps, (0.0, 0.01, 0.02))
        self.assertEqual(result.sample_count, 3)
        self.assertEqual(result.node_outputs["A"], {})
        self._assert_expected_output(result.node_outputs["B"][OUTPUT])
        return result

    def _two_worker_result(self, graph: SimulationGraph, worker_suffix: str):
        content = FMU_PATH.read_bytes()
        asset_sha256 = hashlib.sha256(content).hexdigest()
        with TemporaryDirectory() as temporary_directory_a, TemporaryDirectory() as temporary_directory_b:
            cache_root_a = Path(temporary_directory_a)
            cache_root_b = Path(temporary_directory_b)
            launcher_a = LocalWorkerSubprocess(
                f"worker-two-a-{worker_suffix}",
                cache_root_a,
                startup_timeout=_TIMEOUT_SECONDS,
                shutdown_timeout=_TIMEOUT_SECONDS,
            )
            launcher_b = LocalWorkerSubprocess(
                f"worker-two-b-{worker_suffix}",
                cache_root_b,
                startup_timeout=_TIMEOUT_SECONDS,
                shutdown_timeout=_TIMEOUT_SECONDS,
            )
            transport_a: _RecordingTransport | None = None
            transport_b: _RecordingTransport | None = None
            rpc_a: WorkerRpcClient | None = None
            rpc_b: WorkerRpcClient | None = None
            try:
                descriptor_a = launcher_a.start()
                descriptor_b = launcher_b.start()
                self.assertNotEqual(descriptor_a.worker_id, descriptor_b.worker_id)
                self.assertNotEqual(descriptor_a.endpoint, descriptor_b.endpoint)
                self.assertIsNotNone(launcher_a.pid)
                self.assertIsNotNone(launcher_b.pid)
                self.assertNotEqual(launcher_a.pid, os.getpid())
                self.assertNotEqual(launcher_b.pid, os.getpid())
                self.assertNotEqual(launcher_a.pid, launcher_b.pid)

                validator_a = WorkerProtocolValidator()
                transport_a = _RecordingTransport(
                    TcpWorkerClient(
                        descriptor_a.worker_id,
                        descriptor_a.endpoint,
                        JsonWorkerProtocolCodec(validator_a),
                        validator_a,
                        connect_timeout=_TIMEOUT_SECONDS,
                        handshake_timeout=_TIMEOUT_SECONDS,
                        operation_timeout=_TIMEOUT_SECONDS,
                    )
                )
                validator_b = WorkerProtocolValidator()
                transport_b = _RecordingTransport(
                    TcpWorkerClient(
                        descriptor_b.worker_id,
                        descriptor_b.endpoint,
                        JsonWorkerProtocolCodec(validator_b),
                        validator_b,
                        connect_timeout=_TIMEOUT_SECONDS,
                        handshake_timeout=_TIMEOUT_SECONDS,
                        operation_timeout=_TIMEOUT_SECONDS,
                    )
                )
                rpc_a = WorkerRpcClient(descriptor_a.worker_id, transport_a)
                rpc_b = WorkerRpcClient(descriptor_b.worker_id, transport_b)
                rpc_a.connect()
                rpc_b.connect()

                execution_plan = ExecutionPlan(
                    workers=(descriptor_a, descriptor_b),
                    placements=(
                        NodePlacement("A", PlacementKind.WORKER, descriptor_a.worker_id),
                        NodePlacement("B", PlacementKind.WORKER, descriptor_b.worker_id),
                    ),
                )
                graph_runtime_factory = GraphRuntimeBindingsFactory(
                    _RaisingImporter(),
                    _RaisingRuntimeFactory(),
                    _RaisingRuntimeFactory(),
                )
                runner = GraphSimulationRunner(
                    lambda: graph_runtime_factory.create_with_plan(
                        graph,
                        self.config,
                        execution_plan,
                        {
                            descriptor_a.worker_id: RemoteNodeRuntimeFactory(rpc_a),
                            descriptor_b.worker_id: RemoteNodeRuntimeFactory(rpc_b),
                        },
                    )
                )

                result = runner.run(graph, self.config)
                self._assert_worker_protocol_direction(transport_a, transport_b)
                self._assert_cache(cache_root_a, asset_sha256, content)
                self._assert_cache(cache_root_b, asset_sha256, content)

                self.assertIsNone(rpc_a.request(WorkerMessageType.PING))
                self.assertIsNone(rpc_b.request(WorkerMessageType.PING))
                rpc_a.close()
                rpc_a = None
                self.assertEqual(launcher_a.wait(_TIMEOUT_SECONDS), 0)
                self.assertIsNone(rpc_b.request(WorkerMessageType.PING))
                rpc_b.close()
                rpc_b = None
                self.assertEqual(launcher_b.wait(_TIMEOUT_SECONDS), 0)
                return result
            finally:
                if rpc_a is not None:
                    rpc_a.close()
                elif transport_a is not None:
                    transport_a.close()
                if rpc_b is not None:
                    rpc_b.close()
                elif transport_b is not None:
                    transport_b.close()
                launcher_a.close()
                launcher_b.close()

    def _assert_worker_protocol_direction(
        self,
        transport_a: _RecordingTransport,
        transport_b: _RecordingTransport,
    ) -> None:
        creates_a = [
            request.payload
            for request, _, _ in transport_a.exchanges
            if request.message_type is WorkerMessageType.CREATE_RUNTIME
        ]
        creates_b = [
            request.payload
            for request, _, _ in transport_b.exchanges
            if request.message_type is WorkerMessageType.CREATE_RUNTIME
        ]
        self.assertEqual(len(creates_a), 1)
        self.assertEqual(len(creates_b), 1)
        self.assertIsInstance(creates_a[0], CreateRuntimeRequest)
        self.assertIsInstance(creates_b[0], CreateRuntimeRequest)
        self.assertEqual(creates_a[0].node_id, "A")
        self.assertEqual(creates_b[0].node_id, "B")

        source_reads = [
            response
            for request, response, _ in transport_a.exchanges
            if request.message_type is WorkerMessageType.READ_OUTPUTS
        ]
        self.assertEqual(len(source_reads), 3)
        self.assertTrue(all(response.ok for response in source_reads))
        self.assertTrue(
            all(
                isinstance(response.payload, ReadOutputsResponse)
                for response in source_reads
            )
        )
        for response in source_reads:
            self.assertIn(OUTPUT, response.payload.outputs)
        self.assertTrue(
            math.isclose(
                float(source_reads[0].payload.outputs[OUTPUT]),
                2.0,
                abs_tol=_ABS_TOLERANCE,
            )
        )

        target_inputs = [
            request.payload
            for request, _, _ in transport_b.exchanges
            if request.message_type is WorkerMessageType.SET_INPUTS
        ]
        self.assertEqual(len(target_inputs), 2)
        self.assertTrue(
            all(isinstance(payload, SetInputsRequest) for payload in target_inputs)
        )
        self.assertEqual(
            [dict(payload.values) for payload in target_inputs],
            [{INPUT: 2.0}, {INPUT: 2.0}],
        )

    def _assert_cache(
        self,
        cache_root: Path,
        asset_sha256: str,
        content: bytes,
    ) -> None:
        cache_asset = cache_root / "assets" / f"{asset_sha256}.fmu"
        self.assertTrue(cache_asset.is_file())
        self.assertEqual(cache_asset.read_bytes(), content)

    def _assert_parity(self, local_result, distributed_result) -> None:
        self.assertIs(distributed_result.completion_state, SimulationState.COMPLETED)
        self.assertEqual(distributed_result.completed_steps, 2)
        self.assertEqual(distributed_result.start_time, 0.0)
        self.assertEqual(distributed_result.final_time, 0.02)
        self.assertEqual(distributed_result.timestamps, (0.0, 0.01, 0.02))
        self.assertEqual(distributed_result.timestamps, local_result.timestamps)
        self.assertEqual(distributed_result.sample_count, local_result.sample_count)
        self.assertEqual(distributed_result.node_outputs["A"], {})
        local_b = local_result.node_outputs["B"][OUTPUT]
        distributed_b = distributed_result.node_outputs["B"][OUTPUT]
        self.assertEqual(len(local_b), 3)
        self.assertEqual(len(distributed_b), 3)
        for local, distributed in zip(local_b, distributed_b):
            self.assertTrue(
                math.isclose(
                    float(local),
                    float(distributed),
                    rel_tol=_REL_TOLERANCE,
                    abs_tol=_ABS_TOLERANCE,
                ),
                f"local={local!r}, distributed={distributed!r}",
            )
        self._assert_expected_output(distributed_b)

    def _assert_expected_output(self, values) -> None:
        self.assertEqual(len(values), 3)
        for actual, expected in zip(values, (0.0, 2.0, 2.0)):
            self.assertTrue(
                math.isclose(
                    float(actual),
                    expected,
                    rel_tol=_REL_TOLERANCE,
                    abs_tol=_ABS_TOLERANCE,
                ),
                f"actual={actual!r}, expected={expected!r}",
            )

    def test_two_worker_result_matches_local_jacobi_zoh_baseline(self) -> None:
        graph = self._graph(("A", "B"))
        local_result = self._local_result(graph)

        distributed_result = self._two_worker_result(graph, "normal")

        self._assert_parity(local_result, distributed_result)

    def test_two_worker_result_does_not_depend_on_node_declaration_order(self) -> None:
        graph = self._graph(("B", "A"))
        local_result = self._local_result(graph)

        distributed_result = self._two_worker_result(graph, "reversed")

        self._assert_parity(local_result, distributed_result)
