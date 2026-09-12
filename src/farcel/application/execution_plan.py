"""Pure validation and resolution for future graph execution placement."""

from __future__ import annotations

from farcel.contracts.distributed import (
    ExecutionPlan,
    NodePlacement,
    PlacementKind,
    WorkerDescriptor,
    WorkerEndpoint,
)
from farcel.contracts.graph import SimulationGraph
from farcel.contracts.models import ValidationIssue, ValidationReport


class ExecutionPlanValidator:
    """Validate declarative deployment choices without touching runtime state."""

    def validate(
        self,
        graph: SimulationGraph,
        plan: ExecutionPlan,
    ) -> ValidationReport:
        issues: list[ValidationIssue] = []
        worker_ids = self._validate_workers(plan.workers, issues)
        self._validate_placements(
            graph,
            plan.placements,
            worker_ids,
            issues,
        )
        return ValidationReport(tuple(issues))

    @staticmethod
    def _validate_workers(
        workers: tuple[WorkerDescriptor, ...],
        issues: list[ValidationIssue],
    ) -> set[str]:
        worker_ids: set[str] = set()
        for index, worker in enumerate(workers):
            worker_id = worker.worker_id
            if _is_blank(worker_id):
                issues.append(
                    ValidationIssue(
                        f"workers[{index}].worker_id",
                        "INVALID_WORKER_ID",
                        "worker_id 必须是非空字符串",
                    )
                )
            elif worker_id in worker_ids:
                issues.append(
                    ValidationIssue(
                        f"workers[{index}].worker_id",
                        "DUPLICATE_WORKER_ID",
                        "worker_id 不能重复",
                    )
                )
            else:
                worker_ids.add(worker_id)

            endpoint = worker.endpoint
            host = endpoint.host if isinstance(endpoint, WorkerEndpoint) else None
            port = endpoint.port if isinstance(endpoint, WorkerEndpoint) else None
            if _is_blank(host):
                issues.append(
                    ValidationIssue(
                        f"workers[{index}].endpoint.host",
                        "INVALID_WORKER_HOST",
                        "endpoint.host 必须是非空字符串",
                    )
                )
            if (
                not isinstance(port, int)
                or isinstance(port, bool)
                or not 1 <= port <= 65535
            ):
                issues.append(
                    ValidationIssue(
                        f"workers[{index}].endpoint.port",
                        "INVALID_WORKER_PORT",
                        "endpoint.port 必须是 1 到 65535 的整数",
                    )
                )
        return worker_ids

    @staticmethod
    def _validate_placements(
        graph: SimulationGraph,
        placements: tuple[NodePlacement, ...],
        worker_ids: set[str],
        issues: list[ValidationIssue],
    ) -> None:
        graph_node_ids = {node.node_id for node in graph.nodes}
        placed_node_ids: set[str] = set()
        for index, placement in enumerate(placements):
            node_id = placement.node_id
            if _is_blank(node_id):
                issues.append(
                    ValidationIssue(
                        f"placements[{index}].node_id",
                        "INVALID_PLACEMENT_NODE_ID",
                        "placement.node_id 必须是非空字符串",
                    )
                )
            elif node_id in placed_node_ids:
                issues.append(
                    ValidationIssue(
                        f"placements[{index}].node_id",
                        "DUPLICATE_NODE_PLACEMENT",
                        "每个 node_id 最多只能有一个 placement",
                    )
                )
            else:
                placed_node_ids.add(node_id)
                if node_id not in graph_node_ids:
                    issues.append(
                        ValidationIssue(
                            f"placements[{index}].node_id",
                            "UNKNOWN_PLACEMENT_NODE",
                            "placement.node_id 必须引用 graph 中存在的 node",
                        )
                    )

            if placement.kind is PlacementKind.LOCAL:
                if placement.worker_id is not None:
                    issues.append(
                        ValidationIssue(
                            f"placements[{index}].worker_id",
                            "LOCAL_PLACEMENT_HAS_WORKER",
                            "LOCAL placement 的 worker_id 必须为 None",
                        )
                    )
            elif placement.kind is PlacementKind.WORKER:
                if _is_blank(placement.worker_id):
                    issues.append(
                        ValidationIssue(
                            f"placements[{index}].worker_id",
                            "WORKER_PLACEMENT_MISSING_WORKER",
                            "WORKER placement 必须指定非空 worker_id",
                        )
                    )
                elif placement.worker_id not in worker_ids:
                    issues.append(
                        ValidationIssue(
                            f"placements[{index}].worker_id",
                            "UNKNOWN_PLACEMENT_WORKER",
                            "WORKER placement 引用了未声明的 worker_id",
                        )
                    )
            else:
                issues.append(
                    ValidationIssue(
                        f"placements[{index}].kind",
                        "INVALID_PLACEMENT_KIND",
                        "placement.kind 必须是受支持的 PlacementKind",
                    )
                )


def resolve_node_placement(
    node_id: str,
    plan: ExecutionPlan,
) -> NodePlacement:
    """Return a declared placement or the logical LOCAL default for one node.

    Callers validate a plan before using it for runtime composition. This helper
    intentionally neither validates nor mutates the graph or persistent project.
    """

    return next(
        (
            placement
            for placement in plan.placements
            if placement.node_id == node_id
        ),
        NodePlacement(node_id=node_id),
    )


def _is_blank(value: object) -> bool:
    return not isinstance(value, str) or not value.strip()
