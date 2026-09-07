from __future__ import annotations

import inspect
import unittest

from farcel.application.engine import FarcelEngine
from farcel.contracts import (
    Connection, EngineError, ErrorCode, GraphSimulationConfig, InterfaceCapability,
    InterfaceType, ModelMetadata, ModelNode, ModelNodeConfig, PortReference,
    RunControl, SimulationGraph, SimulationState, StepResult,
)


class _Importer:
    def __init__(self, metadata): self.metadata = metadata; self.loads = []
    def load(self, path): self.loads.append(str(path)); return self.metadata[str(path)]


class _Session:
    def __init__(self, config): self.config = config; self.value = 0.0
    def initialize(self): self.value = self.config.initial_inputs.get("u", 0.0)
    def set_inputs(self, values): self.value = values.get("u", self.value)
    def step(self, current_time, step_size): return StepResult(current_time + step_size, current_time + step_size, step_size)
    def read_outputs(self): return {name: self.value for name in self.config.selected_outputs}
    def terminate(self): pass
    def close(self): pass


class _SessionFactory:
    def __init__(self): self.creates = 0
    def create(self, metadata, config): self.creates += 1; return _Session(config)


class _ModelExchangeSessionFactory:
    def __init__(self): self.creates = 0
    def create(self, metadata, config): self.creates += 1; raise AssertionError("unexpected ME create")


class _SolverFactory:
    def __init__(self): self.creates = 0
    def create(self): self.creates += 1; raise AssertionError("unexpected solver create")


def _metadata():
    from farcel.contracts import VariableMetadata
    return ModelMetadata(
        model_id="model", source_path="model.fmu", fmi_version="2.0", model_name="model",
        interface_types=(InterfaceType.CO_SIMULATION,), executable_interface=InterfaceType.CO_SIMULATION,
        interface_capabilities=(InterfaceCapability(InterfaceType.CO_SIMULATION, can_execute=True),),
        variables=(VariableMetadata("u", 1, "Real", causality="input"),
                   VariableMetadata("y", 2, "Real", causality="output")),
    )


class EngineGraphApiTests(unittest.TestCase):
    def setUp(self):
        self.importer = _Importer({"A.fmu": _metadata(), "B.fmu": _metadata()})
        self.cs = _SessionFactory(); self.me = _ModelExchangeSessionFactory(); self.solver = _SolverFactory()
        self.engine = FarcelEngine(self.importer, self.cs, model_exchange_session_factory=self.me, solver_factory=self.solver)

    @staticmethod
    def _graph():
        return SimulationGraph(
            nodes=(
                ModelNode("A", "A.fmu", ModelNodeConfig(initial_inputs={"u": 2.0})),
                ModelNode("B", "B.fmu", ModelNodeConfig(selected_outputs=("y",))),
            ),
            connections=(Connection(PortReference("A", "y"), PortReference("B", "u")),),
        )

    @staticmethod
    def _config(stop=.02): return GraphSimulationConfig(stop_time=stop, communication_step=.01)

    def test_validate_graph_returns_valid_report_and_invalid_keeps_issue_schema(self):
        report = self.engine.validate_graph(self._graph(), self._config())
        self.assertTrue(report.is_valid)
        with self.assertRaises(EngineError) as raised:
            self.engine.validate_graph(self._graph(), self._config(stop=.025))
        error = raised.exception
        self.assertIs(error.code, ErrorCode.CONFIG_ERROR)
        issue = error.details["issues"][0]
        self.assertEqual(set(issue), {"field", "code", "message"})
        self.assertEqual(issue["field"], "communication_step")
        self.assertEqual(issue["code"], "GRAPH_DURATION_NOT_COMMUNICATION_ALIGNED")

    def test_invalid_graph_inspects_but_never_creates_native_runtime(self):
        with self.assertRaises(EngineError):
            self.engine.run_graph(self._graph(), self._config(stop=.025))
        self.assertEqual((self.cs.creates, self.me.creates, self.solver.creates), (0, 0, 0))

    def test_prestart_cancelled_precedes_validation_and_import(self):
        control = RunControl(); control.request_stop()
        with self.assertRaises(EngineError) as raised:
            self.engine.run_graph(self._graph(), self._config(), control=control)
        self.assertIs(raised.exception.code, ErrorCode.CANCELLED)
        self.assertEqual(self.importer.loads, [])

    def test_run_graph_composes_runner_and_forwards_progress_and_control(self):
        progress = []
        result = self.engine.run_graph(self._graph(), self._config(), on_progress=progress.append)
        self.assertEqual((result.completion_state, result.timestamps, result.node_outputs),
            (SimulationState.COMPLETED, (0.0, .01, .02), {"A": {}, "B": {"y": (0.0, 2.0, 2.0)}}))
        terminal = progress[-1]
        self.assertEqual((terminal.state, terminal.current_time, terminal.completed_steps, terminal.sample_count),
            (SimulationState.COMPLETED, result.final_time, result.completed_steps, result.sample_count))
        self.assertEqual(self.cs.creates, 2)

    def test_run_graph_signature_is_additive_and_run_fmu_keywords_are_unchanged(self):
        graph_parameters = inspect.signature(self.engine.run_graph).parameters
        self.assertEqual(tuple(graph_parameters), ("graph", "config", "control", "on_progress"))
        self.assertTrue(all(graph_parameters[name].kind is inspect.Parameter.KEYWORD_ONLY for name in ("control", "on_progress")))
        self.assertFalse({"on_result_chunk", "result_chunk_size"} & set(graph_parameters))
        fmu_parameters = inspect.signature(self.engine.run_fmu).parameters
        self.assertEqual(tuple(name for name, parameter in fmu_parameters.items() if parameter.kind is inspect.Parameter.KEYWORD_ONLY),
            ("control", "on_progress", "on_result_chunk", "result_chunk_size"))
