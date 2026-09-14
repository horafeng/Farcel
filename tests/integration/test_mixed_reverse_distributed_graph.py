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
from farcel.application.node_runtime import (
    CoSimulationNodeRuntimeFactory,
    ModelExchangeNodeRuntimeFactory,
)
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
from farcel.contracts.worker_protocol import ReadOutputsResponse, WorkerMessageType
from farcel.infrastructure.fmpy import (
    FmpyCvodeSolverFactory,
    FmpyFmi2ModelExchangeSessionFactory,
    FmpyImporter,
    FmpySessionFactory,
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


class _RecordingImporter:
    def __init__(self, delegate) -> None:
        self._delegate = delegate
        self.loaded: list[str] = []

    def load(self, path):
        self.loaded.append(str(path))
        return self._delegate.load(path)


class _RecordingTransport:
    """Transparent real-TCP wrapper that records complete protocol exchanges."""

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


class _RecordingLocalRuntime:
    def __init__(self, delegate) -> None:
        self._delegate = delegate
        self.inputs = []

    def initialize(self) -> None:
        self._delegate.initialize()

    def set_inputs(self, values) -> None:
        self.inputs.append(dict(values))
        self._delegate.set_inputs(values)

    def advance_to(self, target_time) -> None:
        self._delegate.advance_to(target_time)

    def read_outputs(self):
        return self._delegate.read_outputs()

    def terminate(self) -> None:
        self._delegate.terminate()

    def close(self) -> None:
        self._delegate.close()


class _RecordingCoSimulationFactory:
    def __init__(self, delegate) -> None:
        self._delegate = delegate
        self.runtimes = []

    def create(self, metadata, config):
        runtime = _RecordingLocalRuntime(self._delegate.create(metadata, config))
        self.runtimes.append(runtime)
        return runtime


class MixedReverseDistributedGraphIntegrationTests(unittest.TestCase):
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
        return result

    def _mixed_result(self, graph: SimulationGraph, worker_id: str):
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
                        NodePlacement("A", PlacementKind.WORKER, descriptor.worker_id),
                        NodePlacement("B", PlacementKind.LOCAL),
                    ),
                )
                importer = _RecordingImporter(FmpyImporter())
                local_factory = _RecordingCoSimulationFactory(
                    CoSimulationNodeRuntimeFactory(FmpySessionFactory())
                )
                graph_runtime_factory = GraphRuntimeBindingsFactory(
                    importer,
                    local_factory,
                    ModelExchangeNodeRuntimeFactory(
                        FmpyFmi2ModelExchangeSessionFactory(),
                        FmpyCvodeSolverFactory(),
                    ),
                )
                runner = GraphSimulationRunner(
                    lambda: graph_runtime_factory.create_with_plan(
                        graph,
                        self.config,
                        execution_plan,
                        {descriptor.worker_id: RemoteNodeRuntimeFactory(rpc)},
                    )
                )

                result = runner.run(graph, self.config)
                self.assertEqual(importer.loaded, [str(FMU_PATH)])
                self.assertEqual(len(local_factory.runtimes), 1)
                self.assertEqual(local_factory.runtimes[0].inputs, [{INPUT: 2.0}, {INPUT: 2.0}])
                cache_asset = cache_root / "assets" / f"{asset_sha256}.fmu"
                self.assertTrue(cache_asset.is_file())
                self.assertEqual(cache_asset.read_bytes(), content)

                reads = [
                    response.payload
                    for request, response, _ in transport.exchanges
                    if request.message_type is WorkerMessageType.READ_OUTPUTS
                ]
                self.assertEqual(len(reads), 3)
                self.assertTrue(all(isinstance(payload, ReadOutputsResponse) for payload in reads))
                for payload in reads:
                    self.assertIn(OUTPUT, payload.outputs)
                self.assertTrue(math.isclose(float(reads[0].outputs[OUTPUT]), 2.0, abs_tol=_ABS_TOLERANCE))

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

    def _assert_parity(self, local_result, mixed_result) -> None:
        self.assertIs(mixed_result.completion_state, SimulationState.COMPLETED)
        self.assertEqual(mixed_result.completed_steps, 2)
        self.assertEqual(mixed_result.start_time, 0.0)
        self.assertEqual(mixed_result.final_time, 0.02)
        self.assertEqual(mixed_result.timestamps, (0.0, 0.01, 0.02))
        self.assertEqual(mixed_result.timestamps, local_result.timestamps)
        self.assertEqual(mixed_result.sample_count, local_result.sample_count)
        self.assertEqual(mixed_result.node_outputs["A"], {})
        local_b = local_result.node_outputs["B"][OUTPUT]
        mixed_b = mixed_result.node_outputs["B"][OUTPUT]
        self.assertEqual(len(local_b), 3)
        self.assertEqual(len(mixed_b), 3)
        for local, mixed in zip(local_b, mixed_b):
            self.assertTrue(
                math.isclose(
                    float(local),
                    float(mixed),
                    rel_tol=_REL_TOLERANCE,
                    abs_tol=_ABS_TOLERANCE,
                ),
                f"local={local!r}, mixed={mixed!r}",
            )
        for actual, expected in zip(mixed_b, (0.0, 2.0, 2.0)):
            self.assertTrue(math.isclose(float(actual), expected, abs_tol=_ABS_TOLERANCE))

    def test_worker_to_local_result_matches_local_jacobi_zoh_baseline(self) -> None:
        graph = self._graph(("A", "B"))
        local_result = self._local_result(graph)

        mixed_result = self._mixed_result(graph, "worker-mixed-source")

        self._assert_parity(local_result, mixed_result)

    def test_worker_to_local_result_does_not_depend_on_node_declaration_order(self) -> None:
        graph = self._graph(("B", "A"))
        local_result = self._local_result(graph)

        mixed_result = self._mixed_result(graph, "worker-mixed-source-reversed")

        self._assert_parity(local_result, mixed_result)
