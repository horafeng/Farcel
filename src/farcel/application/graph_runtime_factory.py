from __future__ import annotations

from pathlib import Path

from farcel.application.data_router import DataRouter
from farcel.application.graph_validation import build_node_simulation_config
from farcel.application.node_runtime import (
    CoSimulationNodeRuntimeFactory,
    ModelExchangeNodeRuntimeFactory,
    ModelNodeRuntime,
)
from farcel.application.validation import resolve_execution_interface
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.graph import GraphSimulationConfig, SimulationGraph
from farcel.contracts.models import InterfaceType
from farcel.contracts.ports import ModelImporter


class GraphRuntimeBindingsFactory:
    """Create ordered, uninitialized node runtimes for one validated graph."""

    def __init__(
        self,
        importer: ModelImporter,
        co_simulation_factory: CoSimulationNodeRuntimeFactory,
        model_exchange_factory: ModelExchangeNodeRuntimeFactory,
    ) -> None:
        self._importer = importer
        self._co_simulation_factory = co_simulation_factory
        self._model_exchange_factory = model_exchange_factory

    def create(
        self,
        graph: SimulationGraph,
        config: GraphSimulationConfig,
    ) -> tuple[tuple[str, ModelNodeRuntime], ...]:
        dependencies = DataRouter(graph).source_outputs_by_node
        bindings: list[tuple[str, ModelNodeRuntime]] = []
        try:
            for node in graph.nodes:
                effective_outputs = _ordered_union(
                    node.config.selected_outputs,
                    dependencies.get(node.node_id, ()),
                )
                try:
                    metadata = self._importer.load(Path(node.model_path))
                    node_config = build_node_simulation_config(
                        node.config, config, selected_outputs=effective_outputs
                    )
                    interface = resolve_execution_interface(metadata, node_config)
                    if interface is InterfaceType.CO_SIMULATION:
                        runtime = self._co_simulation_factory.create(metadata, node_config)
                    elif interface is InterfaceType.MODEL_EXCHANGE:
                        runtime = self._model_exchange_factory.create(metadata, node_config)
                    else:
                        raise EngineError(
                            ErrorCode.UNSUPPORTED_INTERFACE,
                            "Graph node 没有可执行 runtime interface",
                        )
                except EngineError as error:
                    raise _with_creation_details(error, node.node_id, node.model_path) from None
                except Exception as error:
                    raise EngineError(
                        ErrorCode.INTERNAL_ERROR,
                        "Graph node runtime 创建失败",
                        {
                            "node_id": node.node_id,
                            "model_path": node.model_path,
                            "phase": "runtime_creation",
                            "diagnostic": str(error),
                        },
                    ) from None
                bindings.append((node.node_id, runtime))
        except EngineError as primary:
            cleanup_failures = _close_bindings(bindings)
            if cleanup_failures:
                details = dict(primary.details)
                details["cleanup_failures"] = tuple(cleanup_failures)
                raise EngineError(primary.code, primary.message, details) from None
            raise
        return tuple(bindings)


def _ordered_union(
    selected_outputs: tuple[str, ...], dependencies: tuple[str, ...]
) -> tuple[str, ...]:
    values = list(selected_outputs)
    for dependency in dependencies:
        if dependency not in values:
            values.append(dependency)
    return tuple(values)


def _with_creation_details(
    error: EngineError, node_id: str, model_path: str
) -> EngineError:
    details = dict(error.details)
    details.setdefault("node_id", node_id)
    details.setdefault("model_path", model_path)
    details.setdefault("phase", "runtime_creation")
    return EngineError(error.code, error.message, details)


def _close_bindings(bindings: list[tuple[str, ModelNodeRuntime]]) -> list[dict[str, object]]:
    failures: list[dict[str, object]] = []
    for node_id, runtime in bindings:
        try:
            runtime.close()
        except EngineError as error:
            failures.append(
                {
                    "node_id": node_id,
                    "phase": "close",
                    "code": error.code.value,
                    "message": error.message,
                    "details": dict(error.details),
                }
            )
        except Exception as error:
            failures.append(
                {
                    "node_id": node_id,
                    "phase": "close",
                    "code": ErrorCode.CLEANUP_ERROR.value,
                    "message": str(error),
                    "details": {},
                }
            )
    return failures
