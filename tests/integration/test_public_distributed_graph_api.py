from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from farcel import create_backend
from farcel.contracts import (
    Connection,
    ExecutionPlan,
    GraphSimulationConfig,
    InterfaceType,
    ModelNode,
    ModelNodeConfig,
    NodePlacement,
    PlacementKind,
    PortReference,
    SimulationGraph,
    SimulationState,
    WorkerDescriptor,
    WorkerEndpoint,
)
from farcel.infrastructure.worker_process import LocalWorkerSubprocess


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FMU_PATH = REPOSITORY_ROOT / "examples" / "fmus" / "Feedthrough-fmi2.fmu"
INPUT = "Float64_continuous_input"
OUTPUT = "Float64_continuous_output"
TIMEOUT_SECONDS = 10.0
REL_TOLERANCE = 1e-9
ABS_TOLERANCE = 1e-10


class PublicDistributedGraphApiIntegrationTests(unittest.TestCase):
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
                        initial_inputs={INPUT: 2.0},
                        execution_interface=InterfaceType.CO_SIMULATION,
                    ),
                ),
                ModelNode(
                    "B",
                    str(FMU_PATH),
                    ModelNodeConfig(
                        selected_outputs=(OUTPUT,),
                        execution_interface=InterfaceType.CO_SIMULATION,
                    ),
                ),
            ),
            connections=(
                Connection(PortReference("A", OUTPUT), PortReference("B", INPUT)),
            ),
        )

    def _local_result(self, graph: SimulationGraph):
        backend = create_backend()
        self.assertTrue(backend.validate_graph(graph, self.config).is_valid)
        result = backend.run_graph(graph, self.config)
        self._assert_result(result)
        return result

    def _assert_result(self, result) -> None:
        self.assertIs(result.completion_state, SimulationState.COMPLETED)
        self.assertEqual(result.completed_steps, 2)
        self.assertEqual(result.timestamps, (0.0, 0.01, 0.02))
        self.assertEqual(result.sample_count, 3)
        self.assertEqual(result.node_outputs["A"], {})
        for actual, expected in zip(result.node_outputs["B"][OUTPUT], (0.0, 2.0, 2.0)):
            self.assertTrue(
                math.isclose(
                    float(actual),
                    expected,
                    rel_tol=REL_TOLERANCE,
                    abs_tol=ABS_TOLERANCE,
                ),
                f"actual={actual!r}, expected={expected!r}",
            )

    def _assert_parity(self, local_result, distributed_result) -> None:
        self._assert_result(distributed_result)
        self.assertEqual(distributed_result.timestamps, local_result.timestamps)
        self.assertEqual(distributed_result.completed_steps, local_result.completed_steps)
        self.assertEqual(distributed_result.completion_state, local_result.completion_state)
        self.assertEqual(distributed_result.sample_count, local_result.sample_count)
        for local, distributed in zip(
            local_result.node_outputs["B"][OUTPUT],
            distributed_result.node_outputs["B"][OUTPUT],
        ):
            self.assertTrue(
                math.isclose(
                    float(local),
                    float(distributed),
                    rel_tol=REL_TOLERANCE,
                    abs_tol=ABS_TOLERANCE,
                ),
                f"local={local!r}, distributed={distributed!r}",
            )

    def test_public_mixed_graph_uses_only_placed_worker_and_cleans_connection(self) -> None:
        graph = self._graph()
        local_result = self._local_result(graph)
        content = FMU_PATH.read_bytes()
        asset_sha256 = hashlib.sha256(content).hexdigest()
        with TemporaryDirectory() as directory:
            cache_root = Path(directory)
            launcher = LocalWorkerSubprocess(
                "public-mixed-worker",
                cache_root,
                startup_timeout=TIMEOUT_SECONDS,
                shutdown_timeout=TIMEOUT_SECONDS,
            )
            try:
                descriptor = launcher.start()
                self.assertIsNotNone(launcher.pid)
                self.assertNotEqual(launcher.pid, os.getpid())
                unused = WorkerDescriptor("unused", WorkerEndpoint("127.0.0.1", 1))
                plan = ExecutionPlan(
                    workers=(descriptor, unused),
                    placements=(
                        NodePlacement("A", PlacementKind.LOCAL),
                        NodePlacement("B", PlacementKind.WORKER, descriptor.worker_id),
                    ),
                )

                result = create_backend().run_graph(
                    graph,
                    self.config,
                    execution_plan=plan,
                )

                self._assert_parity(local_result, result)
                cache_asset = cache_root / "assets" / f"{asset_sha256}.fmu"
                self.assertTrue(cache_asset.is_file())
                self.assertEqual(cache_asset.read_bytes(), content)
                self.assertEqual(launcher.wait(TIMEOUT_SECONDS), 0)
            finally:
                launcher.close()

    def test_public_two_worker_graph_uses_one_session_per_placed_worker(self) -> None:
        graph = self._graph()
        local_result = self._local_result(graph)
        content = FMU_PATH.read_bytes()
        asset_sha256 = hashlib.sha256(content).hexdigest()
        with TemporaryDirectory() as directory_a, TemporaryDirectory() as directory_b:
            cache_root_a = Path(directory_a)
            cache_root_b = Path(directory_b)
            launcher_a = LocalWorkerSubprocess(
                "public-worker-a",
                cache_root_a,
                startup_timeout=TIMEOUT_SECONDS,
                shutdown_timeout=TIMEOUT_SECONDS,
            )
            launcher_b = LocalWorkerSubprocess(
                "public-worker-b",
                cache_root_b,
                startup_timeout=TIMEOUT_SECONDS,
                shutdown_timeout=TIMEOUT_SECONDS,
            )
            try:
                descriptor_a = launcher_a.start()
                descriptor_b = launcher_b.start()
                self.assertNotEqual(descriptor_a.worker_id, descriptor_b.worker_id)
                self.assertNotEqual(descriptor_a.endpoint, descriptor_b.endpoint)
                self.assertNotEqual(launcher_a.pid, os.getpid())
                self.assertNotEqual(launcher_b.pid, os.getpid())
                self.assertNotEqual(launcher_a.pid, launcher_b.pid)
                plan = ExecutionPlan(
                    workers=(descriptor_a, descriptor_b),
                    placements=(
                        NodePlacement("A", PlacementKind.WORKER, descriptor_a.worker_id),
                        NodePlacement("B", PlacementKind.WORKER, descriptor_b.worker_id),
                    ),
                )

                result = create_backend().run_graph(
                    graph,
                    self.config,
                    execution_plan=plan,
                )

                self._assert_parity(local_result, result)
                for cache_root in (cache_root_a, cache_root_b):
                    cache_asset = cache_root / "assets" / f"{asset_sha256}.fmu"
                    self.assertTrue(cache_asset.is_file())
                    self.assertEqual(cache_asset.read_bytes(), content)
                self.assertEqual(launcher_a.wait(TIMEOUT_SECONDS), 0)
                self.assertEqual(launcher_b.wait(TIMEOUT_SECONDS), 0)
            finally:
                launcher_a.close()
                launcher_b.close()
