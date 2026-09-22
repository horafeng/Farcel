from __future__ import annotations

import math
from pathlib import Path
import runpy
import unittest

from farcel.application import StandardBlockCatalogLoader
from farcel.contracts import ModelNode, SimulationState


DEMO = Path(__file__).resolve().parents[2] / "examples" / "closed_loop_control_demo.py"


class ClosedLoopControlDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.demo = runpy.run_path(str(DEMO))

    def test_catalog_discovers_all_demo_standard_blocks(self) -> None:
        catalog = StandardBlockCatalogLoader().load()

        self.assertEqual(
            (
                "farcel.sources.step",
                "farcel.math.sum",
                "farcel.control.pid",
                "farcel.continuous.first_order",
                "farcel.math.gain",
            ),
            tuple(catalog.get_block(block_id).block_id for block_id in (
                "farcel.sources.step",
                "farcel.math.sum",
                "farcel.control.pid",
                "farcel.continuous.first_order",
                "farcel.math.gain",
            )),
        )

    def test_factory_built_closed_loop_graph_runs_with_output(self) -> None:
        graph = self.demo["build_closed_loop_graph"]()
        report, result = self.demo["run_demo"]()

        self.assertEqual(
            ("step", "sum", "pid", "first_order", "feedback_gain"),
            tuple(node.node_id for node in graph.nodes),
        )
        self.assertTrue(all(isinstance(node, ModelNode) for node in graph.nodes))
        self.assertEqual(5, len(graph.connections))
        self.assertTrue(report.is_valid)
        self.assertIs(SimulationState.COMPLETED, result.completion_state)
        self.assertTrue(result.timestamps)
        self.assertIn("first_order", result.node_outputs)
        self.assertIn("y", result.node_outputs["first_order"])
        output = result.node_outputs["first_order"]["y"]
        self.assertEqual(len(result.timestamps), len(output))

        # explicit-Jacobi routes prior-checkpoint snapshots, so this verifies
        # control response properties rather than an exact checkpoint curve.
        self.assertTrue(all(math.isfinite(value) for value in output))
        self.assertAlmostEqual(0.0, output[0], places=6)
        self.assertGreater(output[-1], output[0] + 0.1)

        initial_error = abs(1.0 - output[0])
        final_error = abs(1.0 - output[-1])
        self.assertLess(final_error, initial_error)
        self.assertGreater(output[-1], 0.7)
        self.assertLess(output[-1], 1.3)

        self.assertTrue(
            all(
                current > previous
                for previous, current in zip(result.timestamps, result.timestamps[1:])
            )
        )


if __name__ == "__main__":
    unittest.main()
