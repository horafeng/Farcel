from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class DistributedExamplePublicSurfaceTests(unittest.TestCase):
    def test_example_imports_only_public_farcel_modules(self) -> None:
        tree = ast.parse(
            (ROOT / "examples" / "distributed_graph_api_example.py").read_text(encoding="utf-8")
        )
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        self.assertEqual({"farcel", "farcel.contracts"}, {name for name in imports if name.startswith("farcel")})
