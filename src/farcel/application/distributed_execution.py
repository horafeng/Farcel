"""Application seam for future distributed graph execution composition."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from farcel.contracts.distributed import ExecutionPlan
from farcel.contracts.graph import (
    GraphSimulationConfig,
    GraphSimulationResult,
    SimulationGraph,
)
from farcel.contracts.models import RunProgress
from farcel.contracts.run_control import RunControl


class DistributedGraphExecutor(Protocol):
    """Execute a validated graph whose placement requires a Worker."""

    def run(
        self,
        graph: SimulationGraph,
        config: GraphSimulationConfig,
        execution_plan: ExecutionPlan,
        *,
        control: RunControl | None = None,
        on_progress: Callable[[RunProgress], None] | None = None,
    ) -> GraphSimulationResult: ...
