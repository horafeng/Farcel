from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from farcel.application.node_runtime import ModelNodeRuntime


NodeRuntimeBinding = tuple[str, ModelNodeRuntime]
AdvanceOne = Callable[[str, ModelNodeRuntime, float], None]


class NodeAdvanceExecutor(Protocol):
    """Application-internal policy for one graph checkpoint advance phase."""

    def advance_all(
        self,
        nodes: tuple[NodeRuntimeBinding, ...],
        target_time: float,
        advance_one: AdvanceOne,
    ) -> None:
        """Advance every supplied node to one shared logical target."""


class SequentialNodeAdvanceExecutor:
    """Default advance policy preserving graph declaration-order execution."""

    def advance_all(
        self,
        nodes: tuple[NodeRuntimeBinding, ...],
        target_time: float,
        advance_one: AdvanceOne,
    ) -> None:
        for node_id, runtime in nodes:
            advance_one(node_id, runtime, target_time)
