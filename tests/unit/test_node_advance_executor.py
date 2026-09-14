from __future__ import annotations

import unittest

from farcel.application.node_advance_executor import SequentialNodeAdvanceExecutor


class SequentialNodeAdvanceExecutorTests(unittest.TestCase):
    def test_advances_nodes_in_declaration_order_with_shared_target(self):
        nodes = (("A", object()), ("B", object()), ("C", object()))
        calls = []

        SequentialNodeAdvanceExecutor().advance_all(
            nodes,
            0.01,
            lambda node_id, runtime, target_time: calls.append(
                (node_id, runtime, target_time)
            ),
        )

        self.assertEqual(
            calls,
            [("A", nodes[0][1], 0.01), ("B", nodes[1][1], 0.01), ("C", nodes[2][1], 0.01)],
        )

    def test_empty_nodes_are_a_no_op(self):
        calls = []
        SequentialNodeAdvanceExecutor().advance_all((), 0.01, lambda *args: calls.append(args))
        self.assertEqual(calls, [])

    def test_advance_failure_is_propagated_without_retry(self):
        calls = []

        def advance_one(node_id, runtime, target_time):
            calls.append((node_id, runtime, target_time))
            raise RuntimeError("advance failure")

        with self.assertRaisesRegex(RuntimeError, "advance failure"):
            SequentialNodeAdvanceExecutor().advance_all(
                (("A", object()),), 0.01, advance_one
            )
        self.assertEqual(len(calls), 1)
