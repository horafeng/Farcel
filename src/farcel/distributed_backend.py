"""Concrete localhost TCP composition for public distributed graph execution."""

from __future__ import annotations

from collections.abc import Callable

from farcel.application.graph_runner import GraphSimulationRunner
from farcel.application.graph_runtime_factory import GraphRuntimeBindingsFactory
from farcel.application.execution_plan import resolve_node_placement
from farcel.application.remote_runtime_factory import RemoteNodeRuntimeFactory
from farcel.application.worker_client import WorkerRpcClient
from farcel.application.worker_protocol import WorkerProtocolValidator
from farcel.contracts.distributed import ExecutionPlan, PlacementKind, WorkerDescriptor
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.graph import (
    GraphSimulationConfig,
    GraphSimulationResult,
    SimulationGraph,
)
from farcel.contracts.models import RunProgress
from farcel.contracts.run_control import RunControl
from farcel.contracts.worker_protocol import WorkerMessageType
from farcel.infrastructure.worker_protocol.json_codec import JsonWorkerProtocolCodec
from farcel.infrastructure.worker_protocol.tcp_transport import TcpWorkerClient


class TcpDistributedGraphExecutor:
    """Compose existing Worker TCP clients for one public graph run."""

    def __init__(
        self,
        graph_runtime_factory: GraphRuntimeBindingsFactory,
        *,
        connect_timeout: float = 5.0,
        handshake_timeout: float = 5.0,
        operation_timeout: float | None = None,
    ) -> None:
        self._graph_runtime_factory = graph_runtime_factory
        self._connect_timeout = connect_timeout
        self._handshake_timeout = handshake_timeout
        self._operation_timeout = operation_timeout

    def run(
        self,
        graph: SimulationGraph,
        config: GraphSimulationConfig,
        execution_plan: ExecutionPlan,
        *,
        control: RunControl | None = None,
        on_progress: Callable[[RunProgress], None] | None = None,
    ) -> GraphSimulationResult:
        descriptors = {
            descriptor.worker_id: descriptor
            for descriptor in execution_plan.workers
        }
        clients: list[WorkerRpcClient] = []
        remote_factories: dict[str, RemoteNodeRuntimeFactory] = {}
        primary: BaseException | None = None
        result: GraphSimulationResult | None = None
        try:
            for worker_id in _used_worker_ids(graph, execution_plan):
                descriptor = descriptors.get(worker_id)
                if descriptor is None:
                    raise EngineError(
                        ErrorCode.INTERNAL_ERROR,
                        "ExecutionPlan Worker descriptor 缺失",
                        {
                            "phase": "distributed_graph_execution",
                            "worker_id": worker_id,
                        },
                    )
                client = self._connect_client(descriptor)
                clients.append(client)
                client.request(WorkerMessageType.PING)
                remote_factories[worker_id] = RemoteNodeRuntimeFactory(client)

            runner = GraphSimulationRunner(
                lambda: self._graph_runtime_factory.create_with_plan(
                    graph,
                    config,
                    execution_plan,
                    remote_factories,
                )
            )
            result = runner.run(
                graph,
                config,
                control=control,
                on_progress=on_progress,
            )
        except BaseException as error:
            primary = error

        cleanup_failures = _close_clients(clients)
        if primary is not None:
            if isinstance(primary, EngineError) and cleanup_failures:
                details = dict(primary.details)
                details["cleanup_failures"] = tuple(cleanup_failures)
                raise EngineError(primary.code, primary.message, details) from None
            raise primary
        if cleanup_failures:
            first = cleanup_failures[0]
            raise EngineError(
                ErrorCode.CLEANUP_ERROR,
                "Worker connection 清理失败",
                {"cleanup_failures": tuple(cleanup_failures)},
            )
        assert result is not None
        return result

    def _connect_client(self, descriptor: WorkerDescriptor) -> WorkerRpcClient:
        validator = WorkerProtocolValidator()
        transport = TcpWorkerClient(
            descriptor.worker_id,
            descriptor.endpoint,
            JsonWorkerProtocolCodec(validator),
            validator,
            connect_timeout=self._connect_timeout,
            handshake_timeout=self._handshake_timeout,
            operation_timeout=self._operation_timeout,
        )
        client = WorkerRpcClient(descriptor.worker_id, transport)
        client.connect()
        return client


def _used_worker_ids(
    graph: SimulationGraph,
    execution_plan: ExecutionPlan,
) -> tuple[str, ...]:
    worker_ids: list[str] = []
    for node in graph.nodes:
        placement = resolve_node_placement(node.node_id, execution_plan)
        if placement.kind is PlacementKind.WORKER:
            assert placement.worker_id is not None
            if placement.worker_id not in worker_ids:
                worker_ids.append(placement.worker_id)
    return tuple(worker_ids)


def _close_clients(clients: list[WorkerRpcClient]) -> list[dict[str, object]]:
    failures: list[dict[str, object]] = []
    for client in reversed(clients):
        try:
            client.close()
        except EngineError as error:
            failures.append(
                {
                    "worker_id": client.worker_id,
                    "code": error.code.value,
                    "message": error.message,
                    "details": dict(error.details),
                }
            )
        except Exception as error:
            failures.append(
                {
                    "worker_id": client.worker_id,
                    "code": ErrorCode.CLEANUP_ERROR.value,
                    "message": str(error),
                    "details": {},
                }
            )
    return failures
