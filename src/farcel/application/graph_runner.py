from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from farcel.application.data_router import DataRouter
from farcel.application.node_runtime import ModelNodeRuntime
from farcel.application.simulation_orchestrator import SimulationOrchestrator
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.graph import GraphSimulationConfig, GraphSimulationResult, SimulationGraph
from farcel.contracts.models import RunProgress, SimulationState
from farcel.contracts.run_control import RunControl


RuntimeBindingsFactory = Callable[[], tuple[tuple[str, ModelNodeRuntime], ...]]


class GraphSimulationRunner:
    """Application-internal graph whole-run lifecycle over prebuilt runtimes."""

    def __init__(self, runtime_bindings_factory: RuntimeBindingsFactory) -> None:
        self._runtime_bindings_factory = runtime_bindings_factory

    def run(self, graph: SimulationGraph, config: GraphSimulationConfig, *, control: RunControl | None = None, on_progress: Callable[[RunProgress], None] | None = None) -> GraphSimulationResult:
        if control is not None and control.stop_requested:
            raise EngineError(ErrorCode.CANCELLED, "Graph run 在创建 runtime 前已取消")
        bindings: tuple[tuple[str, ModelNodeRuntime], ...] = ()
        primary: EngineError | None = None
        result: GraphSimulationResult | None = None
        try:
            try:
                bindings = self._runtime_bindings_factory()
            except EngineError:
                raise
            except Exception as exc:
                raise EngineError(ErrorCode.INTERNAL_ERROR, "Graph runtime factory 失败", {"diagnostic": str(exc)}) from None
            expected = tuple(node.node_id for node in graph.nodes)
            actual = tuple(node_id for node_id, _ in bindings)
            if actual != expected:
                raise EngineError(ErrorCode.INTERNAL_ERROR, "Graph runtime binding 与 node inventory 不一致", {"phase": "runtime_binding", "expected_node_ids": expected, "actual_node_ids": actual})
            orchestrator = SimulationOrchestrator(bindings, config, DataRouter(graph).route)
            accumulator = _GraphResultAccumulator(graph, config)
            snapshot = orchestrator.initialize()
            accumulator.record(config.start_time, snapshot)
            self._progress(on_progress, config, orchestrator, accumulator, SimulationState.RUNNING)
            stopped = control is not None and control.stop_requested
            while not stopped and not orchestrator.is_complete:
                snapshot = orchestrator.advance_next_checkpoint()
                if orchestrator.completed_steps % accumulator.sample_stride == 0:
                    accumulator.record(orchestrator.current_time, snapshot)
                stopped = control is not None and control.stop_requested
                if not stopped and not orchestrator.is_complete:
                    self._progress(on_progress, config, orchestrator, accumulator, SimulationState.RUNNING)
            if accumulator.final_time != orchestrator.current_time:
                accumulator.record(orchestrator.current_time, snapshot)
            state = SimulationState.STOPPED if stopped else SimulationState.COMPLETED
            result = accumulator.build(config, orchestrator.completed_steps, orchestrator.current_time, state)
        except EngineError as exc:
            primary = exc
        except Exception as exc:
            primary = EngineError(ErrorCode.INTERNAL_ERROR, "Graph run 未预期失败", {"diagnostic": str(exc)})
        cleanup = self._cleanup(bindings)
        if primary is not None:
            if cleanup:
                details = dict(primary.details); details["cleanup_failures"] = tuple(cleanup)
                raise EngineError(primary.code, primary.message, details) from None
            raise primary
        if cleanup:
            first = cleanup[0]
            code = ErrorCode(first["code"])
            raise EngineError(code, first["message"], {"cleanup_failures": tuple(cleanup)})
        assert result is not None
        self._progress(on_progress, config, None, None, result.completion_state, result=result)
        return result

    @staticmethod
    def _cleanup(bindings):
        failures = []
        for phase in ("terminate", "close"):
            for node_id, runtime in bindings:
                try:
                    getattr(runtime, phase)()
                except EngineError as exc:
                    failures.append({"node_id": node_id, "phase": phase, "code": exc.code.value, "message": exc.message, "details": dict(exc.details)})
                except Exception as exc:
                    code = ErrorCode.TERMINATION_ERROR if phase == "terminate" else ErrorCode.CLEANUP_ERROR
                    failures.append({"node_id": node_id, "phase": phase, "code": code.value, "message": str(exc), "details": {}})
        return failures

    @staticmethod
    def _progress(callback, config, orchestrator, accumulator, state, *, result=None):
        if callback is None: return
        if result is None:
            current_time, completed_steps, sample_count = orchestrator.current_time, orchestrator.completed_steps, accumulator.sample_count
        else:
            current_time, completed_steps, sample_count = result.final_time, result.completed_steps, result.sample_count
        progress = RunProgress(config.start_time, config.stop_time, current_time, completed_steps, sample_count, min(1.0, max(0.0, (current_time-config.start_time)/(config.stop_time-config.start_time))), state)
        try: callback(progress)
        except Exception as exc: raise EngineError(ErrorCode.INTERNAL_ERROR, "Graph progress callback 失败", {"phase": "progress_callback", "current_time": current_time, "diagnostic": str(exc)}) from None


class _GraphResultAccumulator:
    def __init__(self, graph, config):
        self._selected = {node.node_id: node.config.selected_outputs for node in graph.nodes}
        self._times = []; self._outputs = {node_id: {name: [] for name in selected} for node_id, selected in self._selected.items()}
        interval = config.output_interval or config.communication_step
        self.sample_stride = round(interval / config.communication_step)
    @property
    def sample_count(self): return len(self._times)
    @property
    def final_time(self): return self._times[-1]
    def record(self, time, snapshot):
        for node_id, selected in self._selected.items():
            values = snapshot.get(node_id, {})
            for name in selected:
                if name not in values:
                    raise EngineError(ErrorCode.OUTPUT_READ_ERROR, "Graph result 缺少所选输出", {"node_id": node_id, "variable_name": name, "phase": "result_sampling", "current_time": time})
                self._outputs[node_id][name].append(values[name])
        self._times.append(time)
    def build(self, config, completed_steps, final_time, state):
        return GraphSimulationResult(config.start_time, config.stop_time, config.communication_step, completed_steps, final_time, state, tuple(self._times), {node: {name: tuple(values) for name, values in outputs.items()} for node, outputs in self._outputs.items()})
