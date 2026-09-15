from __future__ import annotations

import inspect
import unittest

from farcel.application.engine import FarcelEngine
from farcel.contracts import (
    Connection, EngineError, ErrorCode, ExecutionPlan, GraphSimulationConfig,
    GraphSimulationResult, InterfaceCapability, InterfaceType, ModelMetadata,
    ModelNode, ModelNodeConfig, NodePlacement, PlacementKind, PortReference,
    RunControl, SimulationGraph, SimulationState, StepResult, WorkerDescriptor,
    WorkerEndpoint,
)
from farcel.contracts.ports import SimulationEngine


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


class _DistributedExecutor:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def run(self, graph, config, execution_plan, *, control=None, on_progress=None):
        self.calls.append((graph, config, execution_plan, control, on_progress))
        if self.error is not None:
            raise self.error
        return self.result


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

    @staticmethod
    def _worker_plan():
        return ExecutionPlan(
            workers=(WorkerDescriptor("worker-a", WorkerEndpoint("localhost", 9000)),),
            placements=(NodePlacement("B", PlacementKind.WORKER, "worker-a"),),
        )

    @staticmethod
    def _distributed_result():
        return GraphSimulationResult(
            0.0, 0.02, 0.01, 2, 0.02, SimulationState.COMPLETED,
            (0.0, 0.01, 0.02), {"A": {}, "B": {"y": (0.0, 2.0, 2.0)}},
        )

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
        self.assertEqual(tuple(graph_parameters), ("graph", "config", "control", "on_progress", "execution_plan"))
        self.assertTrue(all(graph_parameters[name].kind is inspect.Parameter.KEYWORD_ONLY for name in ("control", "on_progress", "execution_plan")))
        self.assertFalse({"on_result_chunk", "result_chunk_size"} & set(graph_parameters))
        fmu_parameters = inspect.signature(self.engine.run_fmu).parameters
        self.assertEqual(tuple(name for name, parameter in fmu_parameters.items() if parameter.kind is inspect.Parameter.KEYWORD_ONLY),
            ("control", "on_progress", "on_result_chunk", "result_chunk_size"))

    def test_local_only_execution_plans_keep_existing_local_runner_path(self):
        executor = _DistributedExecutor(self._distributed_result())
        engine = FarcelEngine(
            self.importer, self.cs, model_exchange_session_factory=self.me,
            solver_factory=self.solver, distributed_graph_executor=executor,
        )
        local_plans = (
            ExecutionPlan(),
            ExecutionPlan(placements=(NodePlacement("A"), NodePlacement("B"))),
            ExecutionPlan(workers=(WorkerDescriptor("unused", WorkerEndpoint("localhost", 9001)),)),
        )

        for plan in local_plans:
            with self.subTest(plan=plan):
                result = engine.run_graph(self._graph(), self._config(), execution_plan=plan)
                self.assertEqual(result.node_outputs, {"A": {}, "B": {"y": (0.0, 2.0, 2.0)}})

        self.assertEqual(executor.calls, [])
        self.assertEqual(self.cs.creates, 6)

    def test_worker_plan_without_executor_fails_without_local_runtime_creation(self):
        with self.assertRaises(EngineError) as raised:
            self.engine.run_graph(
                self._graph(), self._config(), execution_plan=self._worker_plan()
            )

        error = raised.exception
        self.assertIs(error.code, ErrorCode.NOT_IMPLEMENTED)
        self.assertEqual(error.details, {
            "phase": "distributed_graph_execution",
            "issue_code": "DISTRIBUTED_GRAPH_EXECUTOR_UNAVAILABLE",
        })
        self.assertEqual((self.cs.creates, self.me.creates, self.solver.creates), (0, 0, 0))

    def test_worker_plan_delegates_exact_values_to_injected_executor(self):
        result = self._distributed_result()
        executor = _DistributedExecutor(result)
        engine = FarcelEngine(
            self.importer, self.cs, model_exchange_session_factory=self.me,
            solver_factory=self.solver, distributed_graph_executor=executor,
        )
        graph, config, plan = self._graph(), self._config(), self._worker_plan()
        control = RunControl()
        progress = []

        self.assertIs(
            engine.run_graph(graph, config, execution_plan=plan, control=control, on_progress=progress.append),
            result,
        )
        self.assertEqual(executor.calls, [(graph, config, plan, control, progress.append)])
        self.assertEqual((self.cs.creates, self.me.creates, self.solver.creates), (0, 0, 0))

    def test_prestart_cancellation_precedes_plan_validation_and_executor(self):
        executor = _DistributedExecutor(self._distributed_result())
        engine = FarcelEngine(
            self.importer, self.cs, model_exchange_session_factory=self.me,
            solver_factory=self.solver, distributed_graph_executor=executor,
        )
        control = RunControl(); control.request_stop()

        with self.assertRaises(EngineError) as raised:
            engine.run_graph(
                self._graph(), self._config(), control=control,
                execution_plan=ExecutionPlan(placements=(NodePlacement("B", PlacementKind.WORKER, "missing"),)),
            )

        self.assertIs(raised.exception.code, ErrorCode.CANCELLED)
        self.assertEqual(self.importer.loads, [])
        self.assertEqual(executor.calls, [])
        self.assertEqual(self.cs.creates, 0)

    def test_invalid_plan_uses_public_issue_schema_before_runtime_or_executor(self):
        executor = _DistributedExecutor(self._distributed_result())
        engine = FarcelEngine(
            self.importer, self.cs, model_exchange_session_factory=self.me,
            solver_factory=self.solver, distributed_graph_executor=executor,
        )
        invalid = ExecutionPlan(placements=(NodePlacement("B", PlacementKind.WORKER, "missing"),))

        with self.assertRaises(EngineError) as raised:
            engine.run_graph(self._graph(), self._config(), execution_plan=invalid)

        error = raised.exception
        self.assertIs(error.code, ErrorCode.CONFIG_ERROR)
        self.assertEqual(error.details["issues"][0], {
            "field": "placements[0].worker_id",
            "code": "UNKNOWN_PLACEMENT_WORKER",
            "message": "WORKER placement 引用了未声明的 worker_id",
        })
        self.assertEqual(executor.calls, [])
        self.assertEqual((self.cs.creates, self.me.creates, self.solver.creates), (0, 0, 0))

    def test_validate_execution_plan_is_pure_and_uses_public_validation_errors(self):
        self.assertTrue(self.engine.validate_execution_plan(self._graph(), ExecutionPlan()).is_valid)
        invalid = ExecutionPlan(placements=(NodePlacement("B", PlacementKind.WORKER, "missing"),))

        with self.assertRaises(EngineError) as raised:
            self.engine.validate_execution_plan(self._graph(), invalid)

        self.assertIs(raised.exception.code, ErrorCode.CONFIG_ERROR)
        self.assertEqual(set(raised.exception.details["issues"][0]), {"field", "code", "message"})
        self.assertEqual(self.importer.loads, [])
        self.assertEqual(self.cs.creates, 0)

    def test_worker_executor_engine_error_is_preserved_and_other_error_is_normalized(self):
        original = EngineError(ErrorCode.TIMEOUT, "remote timeout", {"worker": "a"})
        engine = FarcelEngine(
            self.importer, self.cs, model_exchange_session_factory=self.me,
            solver_factory=self.solver, distributed_graph_executor=_DistributedExecutor(error=original),
        )
        with self.assertRaises(EngineError) as preserved:
            engine.run_graph(self._graph(), self._config(), execution_plan=self._worker_plan())
        self.assertIs(preserved.exception, original)

        ordinary = FarcelEngine(
            self.importer, self.cs, model_exchange_session_factory=self.me,
            solver_factory=self.solver, distributed_graph_executor=_DistributedExecutor(error=RuntimeError("boom")),
        )
        with self.assertRaises(EngineError) as normalized:
            ordinary.run_graph(self._graph(), self._config(), execution_plan=self._worker_plan())
        self.assertIs(normalized.exception.code, ErrorCode.INTERNAL_ERROR)
        self.assertEqual(normalized.exception.details, {
            "phase": "distributed_graph_execution", "diagnostic": "boom",
        })

    def test_simulation_engine_protocol_declares_graph_api_surface(self):
        self.assertTrue(hasattr(SimulationEngine, "validate_graph"))
        self.assertTrue(hasattr(SimulationEngine, "validate_execution_plan"))
        parameters = inspect.signature(SimulationEngine.run_graph).parameters
        self.assertEqual(tuple(parameters), ("self", "graph", "config", "control", "on_progress", "execution_plan"))
        self.assertTrue(all(parameters[name].kind is inspect.Parameter.KEYWORD_ONLY for name in ("control", "on_progress", "execution_plan")))
