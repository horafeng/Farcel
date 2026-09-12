from __future__ import annotations

from dataclasses import fields
import unittest

from farcel.application.execution_plan import (
    ExecutionPlanValidator,
    resolve_node_placement,
)
from farcel.contracts import (
    ExecutionPlan,
    ModelNode,
    NodePlacement,
    PlacementKind,
    SimulationGraph,
    WorkerDescriptor,
    WorkerEndpoint,
)


class ExecutionPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = SimulationGraph(
            nodes=(ModelNode("plant", "plant.fmu"), ModelNode("controller", "controller.fmu"))
        )
        self.validator = ExecutionPlanValidator()

    def test_empty_plan_resolves_every_graph_node_to_local(self) -> None:
        plan = ExecutionPlan()

        report = self.validator.validate(self.graph, plan)

        self.assertTrue(report.is_valid)
        self.assertEqual(
            resolve_node_placement("plant", plan),
            NodePlacement("plant"),
        )
        self.assertEqual(
            resolve_node_placement("controller", plan).kind,
            PlacementKind.LOCAL,
        )

    def test_declared_local_and_worker_placements_are_valid(self) -> None:
        plan = ExecutionPlan(
            workers=(self._worker("worker-a"),),
            placements=(
                NodePlacement("plant"),
                NodePlacement("controller", PlacementKind.WORKER, "worker-a"),
            ),
        )

        report = self.validator.validate(self.graph, plan)

        self.assertTrue(report.is_valid)
        self.assertEqual(
            resolve_node_placement("controller", plan).worker_id,
            "worker-a",
        )

    def test_multiple_nodes_may_share_worker_and_worker_may_be_unused(self) -> None:
        shared_plan = ExecutionPlan(
            workers=(self._worker("worker-a"),),
            placements=(
                NodePlacement("plant", PlacementKind.WORKER, "worker-a"),
                NodePlacement("controller", PlacementKind.WORKER, "worker-a"),
            ),
        )
        unused_plan = ExecutionPlan(workers=(self._worker("worker-a"),))

        self.assertTrue(self.validator.validate(self.graph, shared_plan).is_valid)
        self.assertTrue(self.validator.validate(self.graph, unused_plan).is_valid)

    def test_duplicate_and_blank_worker_ids_are_reported(self) -> None:
        plan = ExecutionPlan(
            workers=(
                self._worker("worker-a"),
                self._worker("worker-a"),
                self._worker(" "),
            )
        )

        self.assertEqual(
            _issue_codes(self.validator.validate(self.graph, plan)),
            ("DUPLICATE_WORKER_ID", "INVALID_WORKER_ID"),
        )

    def test_blank_worker_host_is_reported(self) -> None:
        plan = ExecutionPlan(workers=(WorkerDescriptor("worker-a", WorkerEndpoint(" ", 9000)),))

        self.assertEqual(
            _issue_codes(self.validator.validate(self.graph, plan)),
            ("INVALID_WORKER_HOST",),
        )

    def test_invalid_worker_ports_are_reported(self) -> None:
        for port in (0, -1, 65536, True, "9000"):
            with self.subTest(port=port):
                plan = ExecutionPlan(
                    workers=(WorkerDescriptor("worker-a", WorkerEndpoint("localhost", port)),)
                )
                self.assertEqual(
                    _issue_codes(self.validator.validate(self.graph, plan)),
                    ("INVALID_WORKER_PORT",),
                )

    def test_duplicate_and_unknown_placement_nodes_are_reported(self) -> None:
        plan = ExecutionPlan(
            placements=(
                NodePlacement("plant"),
                NodePlacement("plant"),
                NodePlacement("missing"),
            )
        )

        self.assertEqual(
            _issue_codes(self.validator.validate(self.graph, plan)),
            ("DUPLICATE_NODE_PLACEMENT", "UNKNOWN_PLACEMENT_NODE"),
        )

    def test_invalid_or_blank_placement_node_is_reported(self) -> None:
        report = self.validator.validate(
            self.graph,
            ExecutionPlan(placements=(NodePlacement(""),)),
        )

        self.assertEqual(_issue_codes(report), ("INVALID_PLACEMENT_NODE_ID",))
        self.assertEqual(report.issues[0].field, "placements[0].node_id")

    def test_local_placement_cannot_name_worker(self) -> None:
        report = self.validator.validate(
            self.graph,
            ExecutionPlan(placements=(NodePlacement("plant", worker_id="worker-a"),)),
        )

        self.assertEqual(_issue_codes(report), ("LOCAL_PLACEMENT_HAS_WORKER",))
        self.assertEqual(report.issues[0].field, "placements[0].worker_id")

    def test_worker_placement_requires_declared_nonblank_worker(self) -> None:
        missing = ExecutionPlan(
            placements=(NodePlacement("plant", PlacementKind.WORKER),)
        )
        unknown = ExecutionPlan(
            placements=(NodePlacement("plant", PlacementKind.WORKER, "worker-a"),)
        )

        self.assertEqual(
            _issue_codes(self.validator.validate(self.graph, missing)),
            ("WORKER_PLACEMENT_MISSING_WORKER",),
        )
        self.assertEqual(
            _issue_codes(self.validator.validate(self.graph, unknown)),
            ("UNKNOWN_PLACEMENT_WORKER",),
        )

    def test_resolver_does_not_mutate_graph_or_plan(self) -> None:
        plan = ExecutionPlan(workers=(self._worker("worker-a"),))
        original_nodes = self.graph.nodes
        original_placements = plan.placements

        placement = resolve_node_placement("plant", plan)

        self.assertEqual(placement, NodePlacement("plant"))
        self.assertIs(self.graph.nodes, original_nodes)
        self.assertIs(plan.placements, original_placements)
        self.assertEqual(plan, ExecutionPlan(workers=(self._worker("worker-a"),)))

    def test_distributed_contracts_are_plain_immutable_values(self) -> None:
        endpoint = WorkerEndpoint("localhost", 9000)
        descriptor = WorkerDescriptor("worker-a", endpoint)
        plan = ExecutionPlan((descriptor,), (NodePlacement("plant"),))

        self.assertEqual(tuple(field.name for field in fields(WorkerEndpoint)), ("host", "port"))
        self.assertEqual(tuple(field.name for field in fields(WorkerDescriptor)), ("worker_id", "endpoint"))
        self.assertEqual(plan.workers[0].endpoint, endpoint)
        self.assertEqual(plan.placements[0].kind, PlacementKind.LOCAL)
        with self.assertRaises(AttributeError):
            endpoint.host = "other"

    @staticmethod
    def _worker(worker_id: str) -> WorkerDescriptor:
        return WorkerDescriptor(worker_id, WorkerEndpoint("localhost", 9000))


def _issue_codes(report) -> tuple[str, ...]:
    return tuple(issue.code for issue in report.issues)
