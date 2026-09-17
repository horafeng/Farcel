from __future__ import annotations

import ast
import inspect
from pathlib import Path
import unittest

from farcel.contracts.ports import SimulationEngine


ROOT = Path(__file__).resolve().parents[2]


class FrontendBackendDistributedExampleTests(unittest.TestCase):
    def test_example_uses_only_public_farcel_modules(self) -> None:
        tree = ast.parse(
            (ROOT / "examples" / "frontend_backend_distributed_example.py").read_text(
                encoding="utf-8"
            )
        )
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        self.assertEqual(
            {"farcel", "farcel.contracts"},
            {name for name in imported if name.startswith("farcel")},
        )

    def test_public_engine_keeps_execution_plan_keyword(self) -> None:
        parameters = inspect.signature(SimulationEngine.run_graph).parameters
        self.assertEqual(
            tuple(parameters),
            ("self", "graph", "config", "control", "on_progress", "execution_plan"),
        )
        self.assertIs(parameters["execution_plan"].kind, inspect.Parameter.KEYWORD_ONLY)
