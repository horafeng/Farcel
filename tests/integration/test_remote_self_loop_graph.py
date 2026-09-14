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
    AdvanceToRequest,
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
CONT_INPUT = "Float64_continuous_input"
CONT_OUTPUT = "Float64_continuous_output"
DISC_INPUT = "Float64_discrete_input"
DISC_OUTPUT = "Float64_discrete_output"
_TIMEOUT_SECONDS = 10.0
_REL_TOLERANCE = 1e-9
_ABS_TOLERANCE = 1e-10
_EXPECTED_CONT = (1.0, 2.0, 1.0, 2.0)
_EXPECTED_DISC = (2.0, 1.0, 2.0, 1.0)


class _RecordingTransport:
    """Transparent real-TCP wrapper that records complete exchanges."""

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
        raise AssertionError(f"remote self-loop 不得加载 Coordinator 本地 FMU: {path}")


class _RaisingRuntimeFactory:
    def create(self, *args, **kwargs):
        raise AssertionError("remote self-loop 不得创建 Coordinator 本地 runtime")


class RemoteSelfLoopGraphIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(FMU_PATH.is_file(), f"真实 Feedthrough FMU 不存在: {FMU_PATH}")
        self.config = GraphSimulationConfig(
            start_time=0.0,
            stop_time=0.03,
            communication_step=0.01,
        )
        metadata = create_backend().load_fmu(FMU_PATH)
        names = {variable.name for variable in metadata.variables}
        self.assertTrue(
            {CONT_INPUT, CONT_OUTPUT, DISC_INPUT, DISC_OUTPUT}.issubset(names)
        )

    def _graph(self, *, reverse_connections: bool) -> SimulationGraph:
        continuous_to_discrete = Connection(
            PortReference("A", CONT_OUTPUT),
            PortReference("A", DISC_INPUT),
        )
        discrete_to_continuous = Connection(
            PortReference("A", DISC_OUTPUT),
            PortReference("A", CONT_INPUT),
        )
        connections = (
            (discrete_to_continuous, continuous_to_discrete)
            if reverse_connections
            else (continuous_to_discrete, discrete_to_continuous)
        )
        return SimulationGraph(
            nodes=(
                ModelNode(
                    "A",
                    str(FMU_PATH),
                    ModelNodeConfig(
                        initial_inputs={
                            CONT_INPUT: 1.0,
                            DISC_INPUT: 2.0,
                        },
                        selected_outputs=(CONT_OUTPUT, DISC_OUTPUT),
                        execution_interface=InterfaceType.CO_SIMULATION,
                    ),
                ),
            ),
            connections=connections,
        )

    def _local_result(self, graph: SimulationGraph):
        backend = create_backend()
        self.assertTrue(backend.validate_graph(graph, self.config).is_valid)
        result = backend.run_graph(graph, self.config)
        self._assert_result_shape(result)
        self._assert_output_sequence(
            result.node_outputs["A"][CONT_OUTPUT],
            _EXPECTED_CONT,
        )
        self._assert_output_sequence(
            result.node_outputs["A"][DISC_OUTPUT],
            _EXPECTED_DISC,
        )
        return result

    def _remote_result(self, graph: SimulationGraph, worker_id: str):
        content = FMU_PATH.read_bytes()
        asset_sha256 = hashlib.sha256(content).hexdigest()
        with TemporaryDirectory() as temporary_directory:
            cache_root = Path(temporary_directory)
            launcher = LocalWorkerSubprocess(
                worker_id,
                cache_root,
                startup_timeout=_TIMEOUT_SECONDS,
                shutdown_timeout=_TIMEOUT_SECONDS,
            )
            transport: _RecordingTransport | None = None
            rpc: WorkerRpcClient | None = None
            try:
                descriptor = launcher.start()
                self.assertIsNotNone(launcher.pid)
                self.assertNotEqual(launcher.pid, os.getpid())
                validator = WorkerProtocolValidator()
                transport = _RecordingTransport(
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
                rpc = WorkerRpcClient(descriptor.worker_id, transport)
                rpc.connect()
                execution_plan = ExecutionPlan(
                    workers=(descriptor,),
                    placements=(
                        NodePlacement(
                            "A",
                            PlacementKind.WORKER,
                            descriptor.worker_id,
                        ),
                    ),
                )
                graph_runtime_factory = GraphRuntimeBindingsFactory(
                    _RaisingImporter(),
                    _RaisingRuntimeFactory(),
                    _RaisingRuntimeFactory(),
                )
                remote_factory = RemoteNodeRuntimeFactory(rpc)
                runner = GraphSimulationRunner(
                    lambda: graph_runtime_factory.create_with_plan(
                        graph,
                        self.config,
                        execution_plan,
                        {descriptor.worker_id: remote_factory},
                    )
                )

                result = runner.run(graph, self.config)
                self._assert_wire_exchanges(transport)
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

    def _assert_wire_exchanges(self, transport: _RecordingTransport) -> None:
        creates = [
            request.payload
            for request, _, _ in transport.exchanges
            if request.message_type is WorkerMessageType.CREATE_RUNTIME
        ]
        self.assertEqual(len(creates), 1)
        self.assertIsInstance(creates[0], CreateRuntimeRequest)
        self.assertEqual(creates[0].node_id, "A")

        inputs = [
            request.payload
            for request, _, _ in transport.exchanges
            if request.message_type is WorkerMessageType.SET_INPUTS
        ]
        self.assertEqual(len(inputs), 3)
        self.assertTrue(all(isinstance(payload, SetInputsRequest) for payload in inputs))
        self.assertEqual(
            [dict(payload.values) for payload in inputs],
            [
                {CONT_INPUT: 2.0, DISC_INPUT: 1.0},
                {CONT_INPUT: 1.0, DISC_INPUT: 2.0},
                {CONT_INPUT: 2.0, DISC_INPUT: 1.0},
            ],
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
        continuous_outputs = []
        discrete_outputs = []
        for response in reads:
            self.assertIn(CONT_OUTPUT, response.payload.outputs)
            self.assertIn(DISC_OUTPUT, response.payload.outputs)
            continuous_outputs.append(float(response.payload.outputs[CONT_OUTPUT]))
            discrete_outputs.append(float(response.payload.outputs[DISC_OUTPUT]))
        self._assert_output_sequence(continuous_outputs, _EXPECTED_CONT)
        self._assert_output_sequence(discrete_outputs, _EXPECTED_DISC)

        graph_operations = [
            request
            for request, _, _ in transport.exchanges
            if request.message_type
            in {
                WorkerMessageType.INITIALIZE,
                WorkerMessageType.SET_INPUTS,
                WorkerMessageType.ADVANCE_TO,
                WorkerMessageType.READ_OUTPUTS,
            }
        ]
        self.assertEqual(
            [request.message_type for request in graph_operations],
            [
                WorkerMessageType.INITIALIZE,
                WorkerMessageType.READ_OUTPUTS,
                WorkerMessageType.SET_INPUTS,
                WorkerMessageType.ADVANCE_TO,
                WorkerMessageType.READ_OUTPUTS,
                WorkerMessageType.SET_INPUTS,
                WorkerMessageType.ADVANCE_TO,
                WorkerMessageType.READ_OUTPUTS,
                WorkerMessageType.SET_INPUTS,
                WorkerMessageType.ADVANCE_TO,
                WorkerMessageType.READ_OUTPUTS,
            ],
        )
        advances = [
            request.payload
            for request in graph_operations
            if request.message_type is WorkerMessageType.ADVANCE_TO
        ]
        self.assertTrue(all(isinstance(payload, AdvanceToRequest) for payload in advances))
        self.assertEqual(
            [payload.target_time for payload in advances],
            [0.01, 0.02, 0.03],
        )

    def _assert_result_shape(self, result) -> None:
        self.assertIs(result.completion_state, SimulationState.COMPLETED)
        self.assertEqual(result.completed_steps, 3)
        self.assertEqual(result.start_time, 0.0)
        self.assertEqual(result.final_time, 0.03)
        self.assertEqual(result.timestamps, (0.0, 0.01, 0.02, 0.03))
        self.assertEqual(result.sample_count, 4)

    def _assert_parity(self, local_result, remote_result) -> None:
        self._assert_result_shape(remote_result)
        self.assertEqual(remote_result.completion_state, local_result.completion_state)
        self.assertEqual(remote_result.completed_steps, local_result.completed_steps)
        self.assertEqual(remote_result.timestamps, local_result.timestamps)
        self.assertEqual(remote_result.sample_count, local_result.sample_count)
        for output_name, expected in (
            (CONT_OUTPUT, _EXPECTED_CONT),
            (DISC_OUTPUT, _EXPECTED_DISC),
        ):
            local_outputs = local_result.node_outputs["A"][output_name]
            remote_outputs = remote_result.node_outputs["A"][output_name]
            self.assertEqual(len(local_outputs), 4)
            self.assertEqual(len(remote_outputs), 4)
            for local, remote in zip(local_outputs, remote_outputs):
                self.assertTrue(
                    math.isclose(
                        float(local),
                        float(remote),
                        rel_tol=_REL_TOLERANCE,
                        abs_tol=_ABS_TOLERANCE,
                    ),
                    f"output={output_name}, local={local!r}, remote={remote!r}",
                )
            self._assert_output_sequence(remote_outputs, expected)

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

    def test_remote_cross_self_loop_matches_local_previous_snapshot_baseline(self) -> None:
        # The alternating values require both cross-coupled edges to read one
        # immutable previous snapshot rather than a current-step live output.
        graph = self._graph(reverse_connections=False)
        local_result = self._local_result(graph)

        remote_result = self._remote_result(graph, "worker-self-loop")

        self._assert_parity(local_result, remote_result)

    def test_remote_cross_self_loop_does_not_depend_on_connection_declaration_order(
        self,
    ) -> None:
        graph = self._graph(reverse_connections=True)
        local_result = self._local_result(graph)

        remote_result = self._remote_result(graph, "worker-self-loop-reversed")

        self._assert_parity(local_result, remote_result)
