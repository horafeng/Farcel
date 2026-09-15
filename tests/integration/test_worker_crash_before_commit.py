from __future__ import annotations

import hashlib
import os
from pathlib import Path
import signal
from tempfile import TemporaryDirectory
import unittest

from farcel.application.graph_runner import GraphSimulationRunner
from farcel.application.graph_runtime_factory import GraphRuntimeBindingsFactory
from farcel.application.remote_runtime_factory import RemoteNodeRuntimeFactory
from farcel.application.worker_client import WorkerRpcClient
from farcel.application.worker_protocol import WorkerProtocolValidator
from farcel.contracts.distributed import ExecutionPlan, NodePlacement, PlacementKind
from farcel.contracts.errors import EngineError
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
_TARGET = 0.01


class _RecordingTransport:
    """Transparent real-TCP transport wrapper with success and failure evidence."""

    def __init__(self, delegate) -> None:
        self._delegate = delegate
        self.connect_calls = 0
        self.attempts = []
        self.exchanges = []
        self.failures = []

    def connect(self) -> None:
        self.connect_calls += 1
        self._delegate.connect()

    def request(self, request, *, binary_payload=None):
        self.attempts.append((request, binary_payload))
        try:
            response = self._delegate.request(request, binary_payload=binary_payload)
        except Exception as error:
            self.failures.append((request, error))
            raise
        self.exchanges.append((request, response, binary_payload))
        return response

    def close(self) -> None:
        self._delegate.close()


class _RaisingImporter:
    def load(self, path):
        raise AssertionError(f"crash binding 不得加载 Coordinator 本地 FMU: {path}")


class _RaisingRuntimeFactory:
    def create(self, *args, **kwargs):
        raise AssertionError("crash binding 不得创建 Coordinator 本地 runtime")


class _AbruptWorkerCrash:
    """One real cross-platform child-process SIGTERM; never graceful close."""

    def __init__(self, launcher: LocalWorkerSubprocess) -> None:
        self._launcher = launcher
        self.exit_code: int | None = None
        self.pid: int | None = None

    def crash(self) -> None:
        if self.exit_code is not None:
            return
        pid = self._launcher.pid
        if pid is None:
            raise RuntimeError("Worker PID 不可用，无法执行真实 abrupt crash")
        self.pid = pid
        os.kill(pid, signal.SIGTERM)
        self.exit_code = self._launcher.wait(_TIMEOUT_SECONDS)
        if self.exit_code == 0:
            raise RuntimeError("abrupt Worker crash 意外返回零退出码")


class _CrashOnCheckpointReadRuntime:
    """Crashes its real Worker before the second read request is delegated."""

    def __init__(self, delegate, crasher: _AbruptWorkerCrash) -> None:
        self._delegate = delegate
        self._crasher = crasher
        self.read_count = 0

    def initialize(self) -> None:
        self._delegate.initialize()

    def set_inputs(self, values) -> None:
        self._delegate.set_inputs(values)

    def advance_to(self, target_time: float) -> None:
        self._delegate.advance_to(target_time)

    def read_outputs(self):
        self.read_count += 1
        if self.read_count == 2:
            self._crasher.crash()
        return self._delegate.read_outputs()

    def terminate(self) -> None:
        self._delegate.terminate()

    def close(self) -> None:
        self._delegate.close()


class _CrashBeforeFirstAdvanceRuntime:
    """Crashes its real Worker immediately before the first stateful advance."""

    def __init__(self, delegate, crasher: _AbruptWorkerCrash) -> None:
        self._delegate = delegate
        self._crasher = crasher
        self.advance_count = 0

    def initialize(self) -> None:
        self._delegate.initialize()

    def set_inputs(self, values) -> None:
        self._delegate.set_inputs(values)

    def advance_to(self, target_time: float) -> None:
        self.advance_count += 1
        if self.advance_count == 1:
            self._crasher.crash()
        self._delegate.advance_to(target_time)

    def read_outputs(self):
        return self._delegate.read_outputs()

    def terminate(self) -> None:
        self._delegate.terminate()

    def close(self) -> None:
        self._delegate.close()


class WorkerCrashBeforeCommitIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(FMU_PATH.is_file(), f"真实 Feedthrough FMU 不存在: {FMU_PATH}")
        self.config = GraphSimulationConfig(
            start_time=0.0,
            stop_time=0.02,
            communication_step=0.01,
        )

    def _graph(self) -> SimulationGraph:
        return SimulationGraph(
            nodes=(
                ModelNode(
                    "A",
                    str(FMU_PATH),
                    ModelNodeConfig(
                        initial_inputs={INPUT: 1.0},
                        selected_outputs=(OUTPUT,),
                        execution_interface=InterfaceType.CO_SIMULATION,
                    ),
                ),
                ModelNode(
                    "B",
                    str(FMU_PATH),
                    ModelNodeConfig(
                        initial_inputs={INPUT: 2.0},
                        selected_outputs=(OUTPUT,),
                        execution_interface=InterfaceType.CO_SIMULATION,
                    ),
                ),
            ),
            connections=(
                Connection(PortReference("A", OUTPUT), PortReference("B", INPUT)),
                Connection(PortReference("B", OUTPUT), PortReference("A", INPUT)),
            ),
        )

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

    def _run_crash_case(self, wrapper_type, suffix: str):
        content = FMU_PATH.read_bytes()
        asset_sha256 = hashlib.sha256(content).hexdigest()
        graph = self._graph()
        with TemporaryDirectory() as temporary_directory_a, TemporaryDirectory() as temporary_directory_b:
            cache_root_a = Path(temporary_directory_a)
            cache_root_b = Path(temporary_directory_b)
            launcher_a = LocalWorkerSubprocess(
                f"worker-crash-a-{suffix}", cache_root_a,
                startup_timeout=_TIMEOUT_SECONDS, shutdown_timeout=_TIMEOUT_SECONDS,
            )
            launcher_b = LocalWorkerSubprocess(
                f"worker-crash-b-{suffix}", cache_root_b,
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
                crasher = _AbruptWorkerCrash(launcher_a)

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
                        (
                            node_id,
                            wrapper_type(runtime, crasher) if node_id == "A" else runtime,
                        )
                        for node_id, runtime in bindings
                    )

                progress = []
                with self.assertRaises(EngineError) as raised:
                    GraphSimulationRunner(binding_factory).run(
                        graph, self.config, on_progress=progress.append,
                    )
                error = raised.exception
                self.assertEqual(error.details.get("node_id"), "A")
                if wrapper_type is _CrashOnCheckpointReadRuntime:
                    self.assertEqual(error.details.get("current_time"), _TARGET)
                else:
                    self.assertEqual(error.details.get("current_time"), 0.0)
                    self.assertEqual(error.details.get("target_time"), _TARGET)
                self.assertIn(
                    error.details.get("issue_code"),
                    {"WORKER_DISCONNECTED", "WORKER_FRAME_TRUNCATED"},
                )
                self.assertEqual(
                    [(item.state, item.current_time, item.completed_steps, item.sample_count) for item in progress],
                    [(SimulationState.RUNNING, 0.0, 0, 1)],
                )
                self.assertIsNotNone(crasher.exit_code)
                self.assertNotEqual(crasher.exit_code, 0)
                self.assertEqual(launcher_a.poll(), crasher.exit_code)
                self.assertEqual(crasher.pid, launcher_a.pid)
                self.assert_cache(cache_root_a, asset_sha256, content)
                self.assert_cache(cache_root_b, asset_sha256, content)

                cleanup_failures = error.details.get("cleanup_failures", ())
                self.assertTrue(any(item["node_id"] == "A" for item in cleanup_failures))
                self.assertFalse(any(item["node_id"] == "B" for item in cleanup_failures))
                self.assert_success_count(transport_b, WorkerMessageType.TERMINATE, 1)
                self.assert_success_count(transport_b, WorkerMessageType.CLOSE, 1)
                self.assertIsNone(rpc_b.request(WorkerMessageType.PING))
                rpc_b.close()
                rpc_b = None
                self.assertEqual(launcher_b.wait(_TIMEOUT_SECONDS), 0)
                return error, transport_a, transport_b
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

    def assert_cache(self, cache_root: Path, asset_sha256: str, content: bytes) -> None:
        asset = cache_root / "assets" / f"{asset_sha256}.fmu"
        self.assertTrue(asset.is_file())
        self.assertEqual(asset.read_bytes(), content)

    def assert_success_count(self, transport: _RecordingTransport, message_type, expected: int) -> None:
        actual = sum(
            request.message_type is message_type
            for request, _, _ in transport.exchanges
        )
        self.assertEqual(actual, expected)

    @staticmethod
    def _attempts(transport: _RecordingTransport, message_type):
        return [
            request for request, _ in transport.attempts
            if request.message_type is message_type
        ]

    @staticmethod
    def _successes(transport: _RecordingTransport, message_type):
        return [
            request for request, _, _ in transport.exchanges
            if request.message_type is message_type
        ]

    def test_worker_crash_after_advances_before_read_does_not_publish_checkpoint(self) -> None:
        error, transport_a, transport_b = self._run_crash_case(
            _CrashOnCheckpointReadRuntime, "after-advance",
        )

        self.assertEqual(error.details["node_id"], "A")
        self.assertEqual(
            [dict(request.payload.values) for request in self._successes(transport_a, WorkerMessageType.SET_INPUTS)],
            [{INPUT: 2.0}],
        )
        self.assertEqual(
            [dict(request.payload.values) for request in self._successes(transport_b, WorkerMessageType.SET_INPUTS)],
            [{INPUT: 1.0}],
        )
        for transport in (transport_a, transport_b):
            advances = self._successes(transport, WorkerMessageType.ADVANCE_TO)
            self.assertEqual(len(advances), 1)
            self.assertIsInstance(advances[0].payload, AdvanceToRequest)
            self.assertEqual(advances[0].payload.target_time, _TARGET)

        self.assertEqual(len(self._attempts(transport_a, WorkerMessageType.READ_OUTPUTS)), 2)
        self.assertEqual(len(self._successes(transport_a, WorkerMessageType.READ_OUTPUTS)), 1)
        self.assertEqual(len(self._successes(transport_b, WorkerMessageType.READ_OUTPUTS)), 1)
        self.assertEqual(len(self._attempts(transport_b, WorkerMessageType.READ_OUTPUTS)), 1)
        self.assert_success_count(transport_a, WorkerMessageType.CREATE_RUNTIME, 1)
        self.assert_success_count(transport_b, WorkerMessageType.CREATE_RUNTIME, 1)

    def test_dead_worker_advance_is_not_retried_reconnected_or_replayed(self) -> None:
        error, transport_a, transport_b = self._run_crash_case(
            _CrashBeforeFirstAdvanceRuntime, "before-advance",
        )

        self.assertEqual(error.details["node_id"], "A")
        self.assertEqual(len(self._attempts(transport_a, WorkerMessageType.ADVANCE_TO)), 1)
        self.assertEqual(len(self._successes(transport_a, WorkerMessageType.ADVANCE_TO)), 0)
        self.assertEqual(transport_a.connect_calls, 1)
        self.assert_success_count(transport_a, WorkerMessageType.CREATE_RUNTIME, 1)
        self.assertEqual(len(self._successes(transport_b, WorkerMessageType.SET_INPUTS)), 1)
        self.assertEqual(len(self._attempts(transport_b, WorkerMessageType.ADVANCE_TO)), 0)
