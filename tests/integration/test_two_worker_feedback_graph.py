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
_EXPECTED_A = (1.0, 2.0, 1.0, 2.0)
_EXPECTED_B = (2.0, 1.0, 2.0, 1.0)


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
        raise AssertionError(f"feedback binding 不得加载 Coordinator 本地 FMU: {path}")


class _RaisingRuntimeFactory:
    def create(self, *args, **kwargs):
        raise AssertionError("feedback binding 不得创建 Coordinator 本地 runtime")


class TwoWorkerFeedbackGraphIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(FMU_PATH.is_file(), f"真实 Feedthrough FMU 不存在: {FMU_PATH}")
        self.config = GraphSimulationConfig(
            start_time=0.0,
            stop_time=0.03,
            communication_step=0.01,
        )

    def _graph(self, node_order: tuple[str, str]) -> SimulationGraph:
        nodes = {
            "A": ModelNode(
                "A",
                str(FMU_PATH),
                ModelNodeConfig(
                    initial_inputs={INPUT: 1.0},
                    selected_outputs=(OUTPUT,),
                    execution_interface=InterfaceType.CO_SIMULATION,
                ),
            ),
            "B": ModelNode(
                "B",
                str(FMU_PATH),
                ModelNodeConfig(
                    initial_inputs={INPUT: 2.0},
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
                Connection(
                    PortReference("B", OUTPUT),
                    PortReference("A", INPUT),
                ),
            ),
        )

    def _local_result(self, graph: SimulationGraph):
        backend = create_backend()
        self.assertTrue(backend.validate_graph(graph, self.config).is_valid)
        result = backend.run_graph(graph, self.config)
        self._assert_result_shape(result)
        self._assert_output_sequence(result.node_outputs["A"][OUTPUT], _EXPECTED_A)
        self._assert_output_sequence(result.node_outputs["B"][OUTPUT], _EXPECTED_B)
        return result

    def _two_worker_result(self, graph: SimulationGraph, worker_suffix: str):
        content = FMU_PATH.read_bytes()
        asset_sha256 = hashlib.sha256(content).hexdigest()
        with TemporaryDirectory() as temporary_directory_a, TemporaryDirectory() as temporary_directory_b:
            cache_root_a = Path(temporary_directory_a)
            cache_root_b = Path(temporary_directory_b)
            launcher_a = LocalWorkerSubprocess(
                f"worker-feedback-a-{worker_suffix}",
                cache_root_a,
                startup_timeout=_TIMEOUT_SECONDS,
                shutdown_timeout=_TIMEOUT_SECONDS,
            )
            launcher_b = LocalWorkerSubprocess(
                f"worker-feedback-b-{worker_suffix}",
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

                transport_a = self._transport_for(descriptor_a)
                transport_b = self._transport_for(descriptor_b)
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
                self._assert_worker_exchanges(transport_a, "A", _EXPECTED_A, _EXPECTED_B)
                self._assert_worker_exchanges(transport_b, "B", _EXPECTED_B, _EXPECTED_A)
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

    def _transport_for(self, descriptor) -> _RecordingTransport:
        validator = WorkerProtocolValidator()
        return _RecordingTransport(
            TcpWorkerClient(
                descriptor.worker_id,
                descriptor.endpoint,
                JsonWorkerProtocolCodec(validator),
                validator,
                connect_timeout=_TIMEOUT_SECONDS,
                handshake_timeout=_TIMEOUT_SECONDS,
                operation_timeout=_TIMEOUT_SECONDS,
            )
        )

    def _assert_worker_exchanges(
        self,
        transport: _RecordingTransport,
        node_id: str,
        expected_outputs: tuple[float, ...],
        expected_inputs: tuple[float, ...],
    ) -> None:
        creates = [
            request.payload
            for request, _, _ in transport.exchanges
            if request.message_type is WorkerMessageType.CREATE_RUNTIME
        ]
        self.assertEqual(len(creates), 1)
        self.assertIsInstance(creates[0], CreateRuntimeRequest)
        self.assertEqual(creates[0].node_id, node_id)

        inputs = [
            request.payload
            for request, _, _ in transport.exchanges
            if request.message_type is WorkerMessageType.SET_INPUTS
        ]
        self.assertEqual(len(inputs), 3)
        self.assertTrue(all(isinstance(payload, SetInputsRequest) for payload in inputs))
        self.assertEqual(
            [dict(payload.values) for payload in inputs],
            [{INPUT: value} for value in expected_inputs[:3]],
        )

        reads = [
            response
            for request, response, _ in transport.exchanges
            if request.message_type is WorkerMessageType.READ_OUTPUTS
        ]
        self.assertEqual(len(reads), 4)
        self.assertTrue(all(response.ok for response in reads))
        self.assertTrue(
            all(isinstance(response.payload, ReadOutputsResponse) for response in reads)
        )
        outputs = []
        for response in reads:
            self.assertIn(OUTPUT, response.payload.outputs)
            outputs.append(float(response.payload.outputs[OUTPUT]))
        self._assert_output_sequence(outputs, expected_outputs)

    def _assert_cache(
        self,
        cache_root: Path,
        asset_sha256: str,
        content: bytes,
    ) -> None:
        cache_asset = cache_root / "assets" / f"{asset_sha256}.fmu"
        self.assertTrue(cache_asset.is_file())
        self.assertEqual(cache_asset.read_bytes(), content)

    def _assert_result_shape(self, result) -> None:
        self.assertIs(result.completion_state, SimulationState.COMPLETED)
        self.assertEqual(result.completed_steps, 3)
        self.assertEqual(result.start_time, 0.0)
        self.assertEqual(result.final_time, 0.03)
        self.assertEqual(result.timestamps, (0.0, 0.01, 0.02, 0.03))
        self.assertEqual(result.sample_count, 4)

    def _assert_parity(self, local_result, distributed_result) -> None:
        self._assert_result_shape(distributed_result)
        self.assertEqual(distributed_result.completion_state, local_result.completion_state)
        self.assertEqual(distributed_result.completed_steps, local_result.completed_steps)
        self.assertEqual(distributed_result.timestamps, local_result.timestamps)
        self.assertEqual(distributed_result.sample_count, local_result.sample_count)
        for node_id, expected in (("A", _EXPECTED_A), ("B", _EXPECTED_B)):
            local_outputs = local_result.node_outputs[node_id][OUTPUT]
            distributed_outputs = distributed_result.node_outputs[node_id][OUTPUT]
            self.assertEqual(len(local_outputs), 4)
            self.assertEqual(len(distributed_outputs), 4)
            for local, distributed in zip(local_outputs, distributed_outputs):
                self.assertTrue(
                    math.isclose(
                        float(local),
                        float(distributed),
                        rel_tol=_REL_TOLERANCE,
                        abs_tol=_ABS_TOLERANCE,
                    ),
                    f"node={node_id}, local={local!r}, distributed={distributed!r}",
                )
            self._assert_output_sequence(distributed_outputs, expected)

    def _assert_output_sequence(self, values, expected: tuple[float, ...]) -> None:
        self.assertEqual(len(values), len(expected))
        for actual, expected_value in zip(values, expected):
            self.assertTrue(
                math.isclose(
                    float(actual),
                    expected_value,
                    rel_tol=_REL_TOLERANCE,
                    abs_tol=_ABS_TOLERANCE,
                ),
                f"actual={actual!r}, expected={expected_value!r}",
            )

    def test_two_worker_feedback_matches_local_one_checkpoint_delay_baseline(self) -> None:
        # The alternating trajectories prove that both routes use one immutable
        # previous checkpoint, rather than same-checkpoint algebraic feed-through.
        graph = self._graph(("A", "B"))
        local_result = self._local_result(graph)

        distributed_result = self._two_worker_result(graph, "normal")

        self._assert_parity(local_result, distributed_result)

    def test_two_worker_feedback_does_not_depend_on_node_declaration_order(self) -> None:
        graph = self._graph(("B", "A"))
        local_result = self._local_result(graph)

        distributed_result = self._two_worker_result(graph, "reversed")

        self._assert_parity(local_result, distributed_result)
