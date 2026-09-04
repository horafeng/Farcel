from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from farcel.application.node_runtime import ModelNodeRuntime
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.graph import GraphSimulationConfig


Snapshot = Mapping[str, Mapping[str, Any]]
RouteSnapshot = Callable[[Snapshot], Mapping[str, Mapping[str, Any]]]


class SimulationOrchestrator:
    """Application-internal explicit-Jacobi global checkpoint scheduler.

    Runtime construction, routing interpretation, results, stop control, and
    cleanup deliberately remain outside this Phase 4.3 checkpoint primitive.
    """

    def __init__(
        self,
        nodes: tuple[tuple[str, ModelNodeRuntime], ...],
        config: GraphSimulationConfig,
        route_snapshot: RouteSnapshot,
    ) -> None:
        self._nodes = nodes
        self._nodes_by_id = dict(nodes)
        self._config = config
        self._route_snapshot = route_snapshot
        self._total_steps = round(
            (config.stop_time - config.start_time) / config.communication_step
        )
        self._current_time = config.start_time
        self._completed_steps = 0
        self._initialized = False
        self._failed = False
        self._snapshot: Snapshot | None = None

    @property
    def current_time(self) -> float:
        return self._current_time

    @property
    def completed_steps(self) -> int:
        return self._completed_steps

    @property
    def is_complete(self) -> bool:
        return self._completed_steps == self._total_steps

    @property
    def is_failed(self) -> bool:
        return self._failed

    def initialize(self) -> Snapshot:
        if self._failed:
            raise EngineError(ErrorCode.INITIALIZATION_ERROR, "Graph scheduler 已失败")
        if self._initialized:
            assert self._snapshot is not None
            return self._snapshot
        try:
            for _, runtime in self._nodes:
                runtime.initialize()
            snapshot = self._read_snapshot()
        except Exception:
            self._failed = True
            raise
        self._snapshot = snapshot
        self._initialized = True
        return snapshot

    def advance_next_checkpoint(self) -> Snapshot:
        if not self._initialized or self._failed:
            raise EngineError(ErrorCode.STEP_ERROR, "Graph scheduler 状态不允许推进")
        if self.is_complete:
            raise EngineError(ErrorCode.STEP_ERROR, "Graph 已到达 stop_time")
        assert self._snapshot is not None

        target = self._checkpoint_target()
        try:
            routed_inputs = self._materialize_routed_inputs(
                self._route_snapshot(self._snapshot)
            )
            for node_id, runtime in self._nodes:
                runtime.set_inputs(routed_inputs.get(node_id, {}))
            for _, runtime in self._nodes:
                runtime.advance_to(target)
            snapshot = self._read_snapshot()
        except Exception:
            self._failed = True
            raise

        self._completed_steps += 1
        self._current_time = (
            self._config.stop_time if self.is_complete else target
        )
        self._snapshot = snapshot
        return snapshot

    def _checkpoint_target(self) -> float:
        step_index = self._completed_steps + 1
        if step_index == self._total_steps:
            return self._config.stop_time
        return self._config.start_time + step_index * self._config.communication_step

    def _materialize_routed_inputs(
        self, routed_inputs: Mapping[str, Mapping[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        materialized = {
            node_id: dict(values) for node_id, values in dict(routed_inputs).items()
        }
        for node_id in materialized:
            if node_id not in self._nodes_by_id:
                raise EngineError(
                    ErrorCode.INTERNAL_ERROR,
                    "route callback 返回未知 node",
                    {"node_id": node_id, "phase": "routing"},
                )
        return materialized

    def _read_snapshot(self) -> Snapshot:
        return MappingProxyType(
            {
                node_id: MappingProxyType(dict(runtime.read_outputs()))
                for node_id, runtime in self._nodes
            }
        )
