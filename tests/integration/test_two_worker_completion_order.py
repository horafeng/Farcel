from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
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
INPUT = "Float64_continuous_input"
OUTPUT = "Float64_continuous_output"
_TIMEOUT_SECONDS = 10.0
_REL_TOLERANCE = 1e-9
_ABS_TOLERANCE = 1e-10
_TARGETS = (0.01, 0.02, 0.03)
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
        raise AssertionError(f"completion-order binding 不得加载 Coordinator 本地 FMU: {path}")


class _RaisingRuntimeFactory:
    def create(self, *args, **kwargs):
        raise AssertionError("completion-order binding 不得创建 Coordinator 本地 runtime")


class _CheckpointTiming:
    def __init__(self, main_thread_id: int) -> None:
        self.main_thread_id = main_thread_id
        self.first_entered = threading.Event()
        self.second_returned = threading.Event()
        self.invocation_order = []
        self.completion_order = []
        self.read_order = []
        self.thread_ids = {}


class _CompletionOrderController:
    """Deterministically records Coordinator-observed runtime timing only."""

    def __init__(self, first_node: str, second_node: str) -> None:
        self._first_node = first_node
        self._second_node = second_node
        self._lock = threading.Lock()
        self._states = {}
        self._active_target: float | None = None

    def begin_checkpoint(self, target_time: float) -> None:
        with self._lock:
            if target_time in self._states:
                raise RuntimeError(f"duplicate controlled checkpoint: {target_time}")
            self._states[target_time] = _CheckpointTiming(threading.get_ident())
            self._active_target = target_time

    def enter_advance(self, node_id: str, target_time: float) -> None:
        state = self._state(target_time)
        if node_id == self._first_node:
            with self._lock:
                state.invocation_order.append(node_id)
                state.thread_ids[node_id] = threading.get_ident()
            state.first_entered.set()
            return
        if node_id != self._second_node:
            raise RuntimeError(f"unexpected controlled node: {node_id}")
        if not state.first_entered.wait(_TIMEOUT_SECONDS):
            raise RuntimeError("controlled concurrent advance did not observe first invocation")
        with self._lock:
            state.invocation_order.append(node_id)
            state.thread_ids[node_id] = threading.get_ident()

    def wait_for_second_return(self, target_time: float) -> None:
        if not self._state(target_time).second_returned.wait(_TIMEOUT_SECONDS):
            raise RuntimeError("controlled concurrent advance did not observe second completion")

    def record_completion(self, node_id: str, target_time: float) -> None:
        state = self._state(target_time)
        with self._lock:
            state.completion_order.append(node_id)
        if node_id == self._second_node:
            state.second_returned.set()

    def release_second(self, target_time: float) -> None:
        self._state(target_time).second_returned.set()

    def record_read(self, node_id: str) -> None:
        with self._lock:
            if self._active_target is None:
                return
            state = self._states[self._active_target]
            if len(state.completion_order) != 2:
                raise RuntimeError("checkpoint read occurred before advance-all completion")
            state.read_order.append(node_id)

    def assert_orders(self, test_case: unittest.TestCase, targets) -> None:
        test_case.assertEqual(tuple(self._states), tuple(targets))
        for target_time in targets:
            state = self._state(target_time)
            test_case.assertEqual(
                state.invocation_order,
                [self._first_node, self._second_node],
            )
            test_case.assertEqual(
                state.completion_order,
                [self._second_node, self._first_node],
            )
            test_case.assertEqual(
                state.read_order,
                [self._first_node, self._second_node],
            )
            test_case.assertEqual(set(state.thread_ids), {self._first_node, self._second_node})
            test_case.assertNotEqual(
                state.thread_ids[self._first_node],
                state.thread_ids[self._second_node],
            )
            test_case.assertNotEqual(state.thread_ids[self._first_node], state.main_thread_id)
            test_case.assertNotEqual(state.thread_ids[self._second_node], state.main_thread_id)

    def _state(self, target_time: float) -> _CheckpointTiming:
        try:
            return self._states[target_time]
        except KeyError as error:
            raise RuntimeError(f"missing controlled checkpoint: {target_time}") from error


class _GatedAdvanceRuntime:
    """Transparent runtime wrapper with test-only Coordinator timing gates."""

    def __init__(self, node_id: str, delegate, controller: _CompletionOrderController) -> None:
        self._node_id = node_id
        self._delegate = delegate
        self._controller = controller

    def initialize(self) -> None:
        self._delegate.initialize()

    def set_inputs(self, values) -> None:
        self._delegate.set_inputs(values)

    def advance_to(self, target_time: float) -> None:
        self._controller.enter_advance(self._node_id, target_time)
        if self._node_id == self._controller._first_node:
            self._delegate.advance_to(target_time)
            self._controller.wait_for_second_return(target_time)
            return
        self._delegate.advance_to(target_time)

    def read_outputs(self):
        self._controller.record_read(self._node_id)
        return self._delegate.read_outputs()

    def terminate(self) -> None:
        self._delegate.terminate()

    def close(self) -> None:
        self._delegate.close()


class _ControlledConcurrentAdvanceExecutor:
    """Integration-only two-thread executor; never a production scheduler."""

    def __init__(self, controller: _CompletionOrderController) -> None:
        self._controller = controller

    def advance_all(self, nodes, target_time, advance_one) -> None:
        expected_nodes = (self._controller._first_node, self._controller._second_node)
        actual_nodes = tuple(node_id for node_id, _ in nodes)
        if actual_nodes != expected_nodes:
            raise RuntimeError(f"unexpected controlled node inventory: {actual_nodes}")
        self._controller.begin_checkpoint(target_time)
        start = threading.Barrier(len(nodes) + 1, timeout=_TIMEOUT_SECONDS)
        errors = [None] * len(nodes)
        threads = []

        def worker(index, node_id, runtime) -> None:
            try:
                start.wait()
                advance_one(node_id, runtime, target_time)
            except BaseException as error:
                errors[index] = error
            else:
                self._controller.record_completion(node_id, target_time)
            finally:
                if node_id == self._controller._second_node:
                    self._controller.release_second(target_time)

        for index, (node_id, runtime) in enumerate(nodes):
            thread = threading.Thread(target=worker, args=(index, node_id, runtime))
            thread.start()
            threads.append(thread)
        try:
            start.wait()
        except threading.BrokenBarrierError as error:
            raise RuntimeError("controlled concurrent advance start barrier failed") from error
        for thread in threads:
            thread.join(_TIMEOUT_SECONDS)
        if any(thread.is_alive() for thread in threads):
            raise RuntimeError("controlled concurrent advance timeout")
        for error in errors:
            if error is not None:
                raise error


class TwoWorkerCompletionOrderIntegrationTests(unittest.TestCase):
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
                Connection(PortReference("A", OUTPUT), PortReference("B", INPUT)),
                Connection(PortReference("B", OUTPUT), PortReference("A", INPUT)),
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

    def _concurrent_two_worker_result(self, graph: SimulationGraph, suffix: str):
        content = FMU_PATH.read_bytes()
        asset_sha256 = hashlib.sha256(content).hexdigest()
        node_order = tuple(node.node_id for node in graph.nodes)
        controller = _CompletionOrderController(*node_order)
        executor = _ControlledConcurrentAdvanceExecutor(controller)
        with TemporaryDirectory() as temporary_directory_a, TemporaryDirectory() as temporary_directory_b:
            cache_root_a = Path(temporary_directory_a)
            cache_root_b = Path(temporary_directory_b)
            launcher_a = LocalWorkerSubprocess(
                f"worker-order-a-{suffix}", cache_root_a,
                startup_timeout=_TIMEOUT_SECONDS, shutdown_timeout=_TIMEOUT_SECONDS,
            )
            launcher_b = LocalWorkerSubprocess(
                f"worker-order-b-{suffix}", cache_root_b,
                startup_timeout=_TIMEOUT_SECONDS, shutdown_timeout=_TIMEOUT_SECONDS,
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
                    _RaisingImporter(), _RaisingRuntimeFactory(), _RaisingRuntimeFactory(),
                )

                def binding_factory():
                    bindings = graph_runtime_factory.create_with_plan(
                        graph,
                        self.config,
                        execution_plan,
                        {
                            descriptor_a.worker_id: RemoteNodeRuntimeFactory(rpc_a),
                            descriptor_b.worker_id: RemoteNodeRuntimeFactory(rpc_b),
                        },
                    )
                    return tuple(
                        (node_id, _GatedAdvanceRuntime(node_id, runtime, controller))
                        for node_id, runtime in bindings
                    )

                result = GraphSimulationRunner(
                    binding_factory,
                    advance_executor=executor,
                ).run(graph, self.config)
                controller.assert_orders(self, _TARGETS)
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
            request.payload for request, _, _ in transport.exchanges
            if request.message_type is WorkerMessageType.CREATE_RUNTIME
        ]
        self.assertEqual(len(creates), 1)
        self.assertIsInstance(creates[0], CreateRuntimeRequest)
        self.assertEqual(creates[0].node_id, node_id)

        inputs = [
            request.payload for request, _, _ in transport.exchanges
            if request.message_type is WorkerMessageType.SET_INPUTS
        ]
        self.assertEqual(len(inputs), 3)
        self.assertTrue(all(isinstance(payload, SetInputsRequest) for payload in inputs))
        self.assertEqual(
            [dict(payload.values) for payload in inputs],
            [{INPUT: value} for value in expected_inputs[:3]],
        )

        advances = [
            request.payload for request, _, _ in transport.exchanges
            if request.message_type is WorkerMessageType.ADVANCE_TO
        ]
        self.assertEqual(len(advances), 3)
        self.assertTrue(all(isinstance(payload, AdvanceToRequest) for payload in advances))
        self.assertEqual([payload.target_time for payload in advances], list(_TARGETS))

        reads = [
            response for request, response, _ in transport.exchanges
            if request.message_type is WorkerMessageType.READ_OUTPUTS
        ]
        self.assertEqual(len(reads), 4)
        self.assertTrue(all(response.ok for response in reads))
        self.assertTrue(all(isinstance(response.payload, ReadOutputsResponse) for response in reads))
        outputs = []
        for response in reads:
            self.assertIn(OUTPUT, response.payload.outputs)
            outputs.append(float(response.payload.outputs[OUTPUT]))
        self._assert_output_sequence(outputs, expected_outputs)

    def _assert_cache(self, cache_root: Path, asset_sha256: str, content: bytes) -> None:
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
                self.assertTrue(math.isclose(
                    float(local), float(distributed),
                    rel_tol=_REL_TOLERANCE, abs_tol=_ABS_TOLERANCE,
                ))
            self._assert_output_sequence(distributed_outputs, expected)

    def _assert_output_sequence(self, values, expected: tuple[float, ...]) -> None:
        self.assertEqual(len(values), len(expected))
        for actual, expected_value in zip(values, expected):
            self.assertTrue(math.isclose(
                float(actual), expected_value,
                rel_tol=_REL_TOLERANCE, abs_tol=_ABS_TOLERANCE,
            ))

    def test_two_worker_completion_order_inversion_keeps_feedback_parity(self) -> None:
        # A and B advance concurrently.  The timing gate delays the first-declared
        # runtime's return until the second-declared runtime has returned, proving
        # Coordinator-observed completion order cannot affect Jacobi routing.
        graph = self._graph(("A", "B"))
        local_result = self._local_result(graph)
        distributed_result = self._concurrent_two_worker_result(graph, "normal")
        self._assert_parity(local_result, distributed_result)

    def test_reversed_declaration_still_inverts_completion_without_changing_feedback(self) -> None:
        graph = self._graph(("B", "A"))
        local_result = self._local_result(graph)
        distributed_result = self._concurrent_two_worker_result(graph, "reversed")
        self._assert_parity(local_result, distributed_result)
