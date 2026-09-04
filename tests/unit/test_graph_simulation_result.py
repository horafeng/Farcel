from __future__ import annotations
import unittest
from farcel.contracts import GraphSimulationResult, SimulationState

class GraphSimulationResultTests(unittest.TestCase):
    def test_valid_nested_completed_and_stopped_results(self):
        result = GraphSimulationResult(0, 1, .1, 2, .2, SimulationState.COMPLETED, (0, .2), {"A": {"y": (1, 2)}, "B": {}})
        self.assertEqual(result.sample_count, 2); self.assertTrue(result.successful)
        self.assertFalse(GraphSimulationResult(0, 1, .1, 0, 0, SimulationState.STOPPED, (0,), {"A": {}}).successful)
    def test_invalid_time_steps_and_nested_lengths_reject(self):
        cases = [
            dict(timestamps=()), dict(timestamps=(0, float("nan"))), dict(timestamps=(0, 0)),
            dict(timestamps=(.1,), final_time=.1), dict(timestamps=(0,), final_time=.1),
            dict(completed_steps=-1), dict(final_time=2, timestamps=(0, 2)), dict(node_outputs={"A": {"y": (1, 2)}}),
        ]
        for values in cases:
            with self.subTest(values=values):
                base = dict(start_time=0, stop_time=1, step_size=.1, completed_steps=0, final_time=0, completion_state=SimulationState.STOPPED, timestamps=(0,), node_outputs={"A": {}}); base.update(values)
                with self.assertRaises(ValueError): GraphSimulationResult(**base)
