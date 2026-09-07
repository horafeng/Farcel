from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.graph import SimulationGraph


class DataRouter:
    """Route validated graph connections from one previous-checkpoint snapshot."""

    def __init__(self, graph: SimulationGraph) -> None:
        self._connections = graph.connections
        dependencies: dict[str, list[str]] = {}
        for connection in self._connections:
            values = dependencies.setdefault(connection.source.node_id, [])
            if connection.source.variable_name not in values:
                values.append(connection.source.variable_name)
        self._source_outputs_by_node = MappingProxyType(
            {node_id: tuple(values) for node_id, values in dependencies.items()}
        )

    @property
    def source_outputs_by_node(self) -> Mapping[str, tuple[str, ...]]:
        """Required routing reads in first-seen connection declaration order."""
        return self._source_outputs_by_node

    def route(
        self, snapshot: Mapping[str, Mapping[str, Any]]
    ) -> Mapping[str, Mapping[str, Any]]:
        routed: dict[str, dict[str, Any]] = {}
        for connection in self._connections:
            source = connection.source
            target = connection.target
            source_outputs = snapshot.get(source.node_id)
            if source_outputs is None:
                raise self._missing_source_error(connection)
            try:
                value = source_outputs[source.variable_name]
            except KeyError:
                raise self._missing_source_error(connection) from None
            routed.setdefault(target.node_id, {})[target.variable_name] = value
        return MappingProxyType(
            {
                node_id: MappingProxyType(inputs)
                for node_id, inputs in routed.items()
            }
        )

    @staticmethod
    def _missing_source_error(connection) -> EngineError:
        return EngineError(
            ErrorCode.OUTPUT_READ_ERROR,
            "Graph routing source 不在 snapshot 中",
            {
                "phase": "routing",
                "source_node_id": connection.source.node_id,
                "source_variable": connection.source.variable_name,
                "target_node_id": connection.target.node_id,
                "target_variable": connection.target.variable_name,
            },
        )
