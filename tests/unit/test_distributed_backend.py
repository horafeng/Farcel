from __future__ import annotations

import unittest

from farcel.contracts import (
    EngineError,
    ErrorCode,
    ExecutionPlan,
    GraphSimulationConfig,
    ModelNode,
    ModelNodeConfig,
    NodePlacement,
    PlacementKind,
    SimulationGraph,
    WorkerDescriptor,
    WorkerEndpoint,
)
from farcel.contracts.worker_protocol import WorkerMessageType
from farcel.distributed_backend import TcpDistributedGraphExecutor


class _Runtime:
    def __init__(self, node_id: str, events: list[tuple[str, str]]) -> None:
        self._node_id = node_id
        self._events = events
        self._time = 0.0

    def initialize(self) -> None:
        self._events.append(("runtime.initialize", self._node_id))

    def set_inputs(self, values: object) -> None:
        self._events.append(("runtime.set_inputs", self._node_id))

    def advance_to(self, target_time: float) -> None:
        self._events.append(("runtime.advance_to", self._node_id))
        self._time = target_time

    def read_outputs(self) -> dict[str, float]:
        self._events.append(("runtime.read_outputs", self._node_id))
        return {"y": self._time}

    def terminate(self) -> None:
        self._events.append(("runtime.terminate", self._node_id))

    def close(self) -> None:
        self._events.append(("runtime.close", self._node_id))


class _BindingsFactory:
    def __init__(
        self,
        events: list[tuple[str, str]],
        *,
        error: EngineError | None = None,
    ) -> None:
        self._events = events
        self._error = error
        self.calls: list[object] = []

    def create_with_plan(self, graph, config, execution_plan, remote_factories):
        self.calls.append(remote_factories)
        if self._error is not None:
            raise self._error
        return tuple(
            (node.node_id, _Runtime(node.node_id, self._events))
            for node in graph.nodes
        )


class _Client:
    def __init__(
        self,
        worker_id: str,
        events: list[tuple[str, str]],
        *,
        close_error: EngineError | None = None,
    ) -> None:
        self.worker_id = worker_id
        self._events = events
        self._close_error = close_error
        self.close_calls = 0

    def request(self, message_type: WorkerMessageType):
        self._events.append((f"client.request.{message_type.value}", self.worker_id))
        if message_type is not WorkerMessageType.PING:
            raise AssertionError(f"unexpected Worker command: {message_type}")
        return None

    def close(self) -> None:
        self.close_calls += 1
        self._events.append(("client.close", self.worker_id))
        if self._close_error is not None:
            raise self._close_error


def _graph(*node_ids: str) -> SimulationGraph:
    return SimulationGraph(
        nodes=tuple(
            ModelNode(node_id, f"{node_id}.fmu", ModelNodeConfig(selected_outputs=("y",)))
            for node_id in node_ids
        )
    )


def _plan(*worker_ids: str) -> ExecutionPlan:
    return ExecutionPlan(
        workers=tuple(
            WorkerDescriptor(worker_id, WorkerEndpoint("127.0.0.1", 9000 + index))
            for index, worker_id in enumerate(worker_ids)
        ),
        placements=tuple(
            NodePlacement(f"node-{index + 1}", PlacementKind.WORKER, worker_id)
            for index, worker_id in enumerate(worker_ids)
        ),
    )


class TcpDistributedGraphExecutorLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = GraphSimulationConfig(stop_time=0.01, communication_step=0.01)

    @staticmethod
    def _executor(factory: _BindingsFactory, connect):
        executor = TcpDistributedGraphExecutor(factory)
        executor._connect_client = connect  # type: ignore[method-assign]
        return executor

    def test_normal_worker_run_terminates_runtime_before_closing_owned_connection(self) -> None:
        events: list[tuple[str, str]] = []
        factory = _BindingsFactory(events)
        client = _Client("worker-a", events)
        executor = self._executor(factory, lambda descriptor: client)

        result = executor.run(_graph("node-1"), self.config, _plan("worker-a"))

        self.assertEqual(result.completed_steps, 1)
        self.assertEqual(client.close_calls, 1)
        self.assertEqual(factory.calls[0].keys(), {"worker-a"})
        self.assertEqual(
            events[-3:],
            [
                ("runtime.terminate", "node-1"),
                ("runtime.close", "node-1"),
                ("client.close", "worker-a"),
            ],
        )

    def test_connection_failure_never_constructs_runtime_bindings(self) -> None:
        events: list[tuple[str, str]] = []
        factory = _BindingsFactory(events)
        failure = EngineError(ErrorCode.TIMEOUT, "connect failed")
        executor = self._executor(factory, lambda descriptor: (_ for _ in ()).throw(failure))

        with self.assertRaises(EngineError) as raised:
            executor.run(_graph("node-1"), self.config, _plan("worker-a"))

        self.assertIs(raised.exception, failure)
        self.assertEqual(factory.calls, [])
        self.assertEqual(events, [])

    def test_partial_worker_startup_failure_closes_previously_owned_connection(self) -> None:
        events: list[tuple[str, str]] = []
        factory = _BindingsFactory(events)
        first = _Client("worker-a", events)
        failure = EngineError(ErrorCode.TIMEOUT, "worker-b unavailable")
        descriptors_seen: list[str] = []

        def connect(descriptor):
            descriptors_seen.append(descriptor.worker_id)
            if descriptor.worker_id == "worker-a":
                return first
            raise failure

        executor = self._executor(factory, connect)
        with self.assertRaises(EngineError) as raised:
            executor.run(
                _graph("node-1", "node-2"),
                self.config,
                _plan("worker-a", "worker-b"),
            )

        self.assertIs(raised.exception, failure)
        self.assertEqual(descriptors_seen, ["worker-a", "worker-b"])
        self.assertEqual(first.close_calls, 1)
        self.assertEqual(factory.calls, [])

    def test_execution_failure_closes_all_connected_workers_in_reverse_order(self) -> None:
        events: list[tuple[str, str]] = []
        failure = EngineError(ErrorCode.STEP_ERROR, "graph run failed")
        factory = _BindingsFactory(events, error=failure)
        clients = {
            "worker-a": _Client("worker-a", events),
            "worker-b": _Client("worker-b", events),
        }
        executor = self._executor(factory, lambda descriptor: clients[descriptor.worker_id])

        with self.assertRaises(EngineError) as raised:
            executor.run(
                _graph("node-1", "node-2"),
                self.config,
                _plan("worker-a", "worker-b"),
            )

        self.assertIs(raised.exception, failure)
        self.assertEqual([event for event in events if event[0] == "client.close"], [
            ("client.close", "worker-b"),
            ("client.close", "worker-a"),
        ])

    def test_repeated_runs_close_only_the_connections_owned_by_each_run(self) -> None:
        events: list[tuple[str, str]] = []
        factory = _BindingsFactory(events)
        first = _Client("worker-a", events)
        second = _Client("worker-a", events)
        clients = iter((first, second))
        executor = self._executor(factory, lambda descriptor: next(clients))

        executor.run(_graph("node-1"), self.config, _plan("worker-a"))
        executor.run(_graph("node-1"), self.config, _plan("worker-a"))

        self.assertEqual((first.close_calls, second.close_calls), (1, 1))
        self.assertEqual(len(factory.calls), 2)

    def test_cleanup_error_is_attached_without_replacing_primary_execution_error(self) -> None:
        events: list[tuple[str, str]] = []
        primary = EngineError(ErrorCode.STEP_ERROR, "advance failed", {"node_id": "node-1"})
        cleanup = EngineError(ErrorCode.CLEANUP_ERROR, "socket close failed")
        factory = _BindingsFactory(events, error=primary)
        client = _Client("worker-a", events, close_error=cleanup)
        executor = self._executor(factory, lambda descriptor: client)

        with self.assertRaises(EngineError) as raised:
            executor.run(_graph("node-1"), self.config, _plan("worker-a"))

        error = raised.exception
        self.assertIs(error.code, ErrorCode.STEP_ERROR)
        self.assertEqual(error.message, "advance failed")
        self.assertEqual(error.details["node_id"], "node-1")
        self.assertEqual(error.details["cleanup_failures"][0]["worker_id"], "worker-a")
        self.assertEqual(error.details["cleanup_failures"][0]["code"], ErrorCode.CLEANUP_ERROR.value)
