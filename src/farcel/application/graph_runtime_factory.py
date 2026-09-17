from __future__ import annotations

from pathlib import Path
from typing import Mapping, Protocol

from farcel.application.data_router import DataRouter
from farcel.application.execution_plan import (
    ExecutionPlanValidator,
    resolve_node_placement,
)
from farcel.application.graph_validation import build_node_simulation_config
from farcel.application.node_runtime import (
    CoSimulationNodeRuntimeFactory,
    ModelExchangeNodeRuntimeFactory,
    ModelNodeRuntime,
)
from farcel.application.validation import resolve_execution_interface
from farcel.contracts.distributed import ExecutionPlan, PlacementKind
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.graph import GraphSimulationConfig, SimulationGraph
from farcel.contracts.models import InterfaceType, SimulationConfig
from farcel.contracts.ports import ModelImporter


class RemoteRuntimeProvisioner(Protocol):
    """Application composition seam for one already-connected Worker."""

    def create(
        self,
        node_id: str,
        model_path: str | Path,
        config: SimulationConfig,
    ) -> ModelNodeRuntime:
        ...


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
        """Create local-only bindings with the original Phase 4 behavior."""

        return self._create_bindings(graph, config, None, None)

    def create_with_plan(
        self,
        graph: SimulationGraph,
        config: GraphSimulationConfig,
        execution_plan: ExecutionPlan,
        remote_runtime_factories: Mapping[str, RemoteRuntimeProvisioner],
    ) -> tuple[tuple[str, ModelNodeRuntime], ...]:
        """Bind graph nodes according to a validated declarative placement plan."""

        report = ExecutionPlanValidator().validate(graph, execution_plan)
        if not report.is_valid:
            raise EngineError(
                ErrorCode.VALIDATION_ERROR,
                "ExecutionPlan 无效",
                {
                    "phase": "execution_plan_validation",
                    "issues": tuple(
                        {
                            "field": issue.field,
                            "code": issue.code,
                            "message": issue.message,
                        }
                        for issue in report.issues
                    ),
                },
            )
        return self._create_bindings(
            graph,
            config,
            execution_plan,
            remote_runtime_factories,
        )

    def _create_bindings(
        self,
        graph: SimulationGraph,
        config: GraphSimulationConfig,
        execution_plan: ExecutionPlan | None,
        remote_runtime_factories: Mapping[str, RemoteRuntimeProvisioner] | None,
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
                    node_config = build_node_simulation_config(
                        node.config, config, selected_outputs=effective_outputs
                    )
                    if execution_plan is None:
                        runtime = self._create_local_runtime(node.model_path, node_config)
                    else:
                        placement = resolve_node_placement(
                            node.node_id, execution_plan
                        )
                        if placement.kind is PlacementKind.LOCAL:
                            runtime = self._create_local_runtime(
                                node.model_path, node_config
                            )
                        else:
                            runtime = self._create_remote_runtime(
                                node.node_id,
                                node.model_path,
                                node_config,
                                placement.worker_id,
                                remote_runtime_factories,
                            )
                except EngineError as error:
                    raise _with_creation_details(
                        error,
                        node.node_id,
                        node.model_path,
                    ) from None
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

    def _create_local_runtime(
        self,
        model_path: str,
        node_config: SimulationConfig,
    ) -> ModelNodeRuntime:
        metadata = self._importer.load(Path(model_path))
        interface = resolve_execution_interface(metadata, node_config)
        if interface is InterfaceType.CO_SIMULATION:
            return self._co_simulation_factory.create(metadata, node_config)
        if interface is InterfaceType.MODEL_EXCHANGE:
            return self._model_exchange_factory.create(metadata, node_config)
        raise EngineError(
            ErrorCode.UNSUPPORTED_INTERFACE,
            "Graph node 没有可执行 runtime interface",
        )

    @staticmethod
    def _create_remote_runtime(
        node_id: str,
        model_path: str,
        node_config: SimulationConfig,
        worker_id: str | None,
        remote_runtime_factories: Mapping[str, RemoteRuntimeProvisioner] | None,
    ) -> ModelNodeRuntime:
        if (
            not isinstance(worker_id, str)
            or not worker_id.strip()
            or remote_runtime_factories is None
            or worker_id not in remote_runtime_factories
        ):
            raise EngineError(
                ErrorCode.CONFIG_ERROR,
                "Worker runtime provisioner 不可用",
                {
                    "phase": "runtime_creation",
                    "issue_code": "REMOTE_RUNTIME_FACTORY_UNAVAILABLE",
                    "node_id": node_id,
                    "worker_id": worker_id,
                },
            )
        try:
            return remote_runtime_factories[worker_id].create(
                node_id,
                model_path,
                node_config,
            )
        except EngineError as error:
            raise _with_creation_details(
                error,
                node_id,
                model_path,
                worker_id=worker_id,
            ) from None
        except Exception as error:
            raise EngineError(
                ErrorCode.INTERNAL_ERROR,
                "Graph remote node runtime 创建失败",
                {
                    "phase": "runtime_creation",
                    "node_id": node_id,
                    "model_path": model_path,
                    "worker_id": worker_id,
                    "diagnostic": str(error),
                },
            ) from None


def _ordered_union(
    selected_outputs: tuple[str, ...], dependencies: tuple[str, ...]
) -> tuple[str, ...]:
    values = list(selected_outputs)
    for dependency in dependencies:
        if dependency not in values:
            values.append(dependency)
    return tuple(values)


def _with_creation_details(
    error: EngineError,
    node_id: str,
    model_path: str,
    *,
    worker_id: str | None = None,
) -> EngineError:
    details = dict(error.details)
    details.setdefault("node_id", node_id)
    details.setdefault("model_path", model_path)
    details.setdefault("phase", "runtime_creation")
    if worker_id is not None:
        details.setdefault("worker_id", worker_id)
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
