from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from farcel import RunControl, create_backend
from farcel.contracts import (
    Connection, EngineError, ErrorCode, GraphSimulationConfig, GraphSimulationResult,
    InterfaceType, ModelNode, ModelNodeConfig, PortReference, SimulationGraph,
    SimulationState,
)


ROOT = Path(__file__).resolve().parents[2]
FMU = ROOT / "examples" / "fmus" / "Feedthrough-fmi2.fmu"
INPUT = "Float64_continuous_input"
OUTPUT = "Float64_continuous_output"


class PublicGraphApiTests(unittest.TestCase):
    @staticmethod
    def _graph():
        return SimulationGraph(
            nodes=(
                ModelNode("A", str(FMU), ModelNodeConfig(initial_inputs={INPUT: 2.0}, execution_interface=InterfaceType.CO_SIMULATION)),
                ModelNode("B", str(FMU), ModelNodeConfig(selected_outputs=(OUTPUT,), execution_interface=InterfaceType.CO_SIMULATION)),
            ),
            connections=(Connection(PortReference("A", OUTPUT), PortReference("B", INPUT)),),
        )

    @staticmethod
    def _config(): return GraphSimulationConfig(stop_time=.02, communication_step=.01)

    @unittest.skipUnless(FMU.is_file(), "FMI2 Feedthrough is unavailable")
    def test_public_create_backend_validates_runs_and_reports_global_progress(self):
        backend = create_backend(); progress = []
        report = backend.validate_graph(self._graph(), self._config())
        result = backend.run_graph(self._graph(), self._config(), on_progress=progress.append)
        self.assertTrue(report.is_valid)
        self.assertIsInstance(result, GraphSimulationResult)
        self.assertEqual((result.completion_state, result.completed_steps, result.timestamps),
            (SimulationState.COMPLETED, 2, (0.0, .01, .02)))
        self.assertEqual(result.node_outputs, {"A": {}, "B": {OUTPUT: (0.0, 2.0, 2.0)}})
        terminal = progress[-1]
        self.assertEqual((terminal.state, terminal.current_time, terminal.completed_steps, terminal.sample_count),
            (SimulationState.COMPLETED, result.final_time, result.completed_steps, result.sample_count))

    @unittest.skipUnless(FMU.is_file(), "FMI2 Feedthrough is unavailable")
    def test_public_stop_returns_graph_result_and_backend_is_reusable(self):
        backend = create_backend(); control = RunControl()
        stopped = backend.run_graph(self._graph(), self._config(), control=control,
            on_progress=lambda _: control.request_stop())
        self.assertEqual((stopped.completion_state, stopped.completed_steps, stopped.final_time),
            (SimulationState.STOPPED, 0, 0.0))
        self.assertIs(backend.run_graph(self._graph(), self._config()).completion_state,
            SimulationState.COMPLETED)

    def test_public_invalid_graph_returns_graph_validator_issue_schema(self):
        backend = create_backend()
        with self.assertRaises(EngineError) as raised:
            backend.validate_graph(self._graph(), GraphSimulationConfig(stop_time=.025, communication_step=.01))
        self.assertIs(raised.exception.code, ErrorCode.CONFIG_ERROR)
        self.assertIn("GRAPH_DURATION_NOT_COMMUNICATION_ALIGNED",
            {issue["code"] for issue in raised.exception.details["issues"]})

    @unittest.skipUnless(FMU.is_file(), "FMI2 Feedthrough is unavailable")
    def test_external_installed_consumer_uses_only_public_graph_contracts(self):
        script = """
import sys
from pathlib import Path
from farcel import create_backend
from farcel.contracts import (
    Connection, GraphSimulationConfig, GraphSimulationResult, InterfaceType, ModelNode,
    ModelNodeConfig, PortReference, SimulationGraph, SimulationState,
)
path = Path(sys.argv[1])
graph = SimulationGraph(nodes=(
    ModelNode('A', str(path), ModelNodeConfig(initial_inputs={'Float64_continuous_input': 2.0}, execution_interface=InterfaceType.CO_SIMULATION)),
    ModelNode('B', str(path), ModelNodeConfig(selected_outputs=('Float64_continuous_output',), execution_interface=InterfaceType.CO_SIMULATION)),
), connections=(Connection(PortReference('A', 'Float64_continuous_output'), PortReference('B', 'Float64_continuous_input')),))
backend = create_backend()
assert backend.validate_graph(graph, GraphSimulationConfig(stop_time=.02, communication_step=.01)).is_valid
result = backend.run_graph(graph, GraphSimulationConfig(stop_time=.02, communication_step=.01))
assert isinstance(result, GraphSimulationResult)
assert result.completion_state is SimulationState.COMPLETED
assert result.node_outputs['A'] == {}
assert result.node_outputs['B']['Float64_continuous_output'] == (0.0, 2.0, 2.0)
print('external graph consumer OK')
"""
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run([sys.executable, "-c", script, str(FMU.resolve())],
                cwd=directory, check=False, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "external graph consumer OK")

    def test_graph_example_imports_only_public_farcel_modules(self):
        tree = ast.parse((ROOT / "examples" / "graph_api_example.py").read_text(encoding="utf-8"))
        imported = {node.module for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) and node.module}
        self.assertEqual({"farcel", "farcel.contracts"},
            {name for name in imported if name.startswith("farcel")})
