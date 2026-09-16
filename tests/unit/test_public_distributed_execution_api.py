from __future__ import annotations

import inspect
import unittest

from farcel import create_backend
from farcel.contracts import (
    ExecutionPlan,
    ModelNode,
    ModelNodeConfig,
    NodePlacement,
    PlacementKind,
    SimulationEngine,
    SimulationGraph,
    WorkerDescriptor,
    WorkerEndpoint,
)


class PublicDistributedExecutionApiTests(unittest.TestCase):
    def test_backend_exposes_additive_public_execution_plan_contract(self) -> None:
        graph = SimulationGraph(nodes=(ModelNode("node-a", "node-a.fmu", ModelNodeConfig()),))
        plan = ExecutionPlan(
            workers=(WorkerDescriptor("worker-a", WorkerEndpoint("127.0.0.1", 9000)),),
            placements=(NodePlacement("node-a", PlacementKind.WORKER, "worker-a"),),
        )

        backend = create_backend()
        self.assertTrue(backend.validate_execution_plan(graph, plan).is_valid)
        self.assertEqual(
            tuple(inspect.signature(backend.run_graph).parameters),
            ("graph", "config", "control", "on_progress", "execution_plan"),
        )
        self.assertEqual(
            tuple(inspect.signature(SimulationEngine.run_graph).parameters),
            ("self", "graph", "config", "control", "on_progress", "execution_plan"),
        )
        self.assertIs(
            inspect.signature(backend.run_graph).parameters["execution_plan"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
