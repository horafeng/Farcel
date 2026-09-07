from __future__ import annotations

import math
from pathlib import Path

from farcel.application.validation import (
    resolve_effective_shapes,
    validate_config,
    validate_timing_config,
)
from farcel.contracts.errors import EngineError
from farcel.contracts.graph import (
    Connection,
    GraphSimulationConfig,
    ModelNode,
    ModelNodeConfig,
    PortReference,
    SimulationGraph,
)
from farcel.contracts.models import (
    ModelMetadata,
    SimulationConfig,
    ValidationIssue,
    ValidationReport,
    VariableMetadata,
)
from farcel.contracts.ports import ModelImporter


class GraphValidator:
    """Validate a declarative graph through metadata inspection only."""

    def __init__(self, importer: ModelImporter) -> None:
        self._importer = importer

    def validate(
        self,
        graph: SimulationGraph,
        config: GraphSimulationConfig,
    ) -> ValidationReport:
        timing_issues = validate_timing_config(
            config.schema_version,
            config.start_time,
            config.stop_time,
            config.communication_step,
            config.output_interval,
        )
        issues = list(timing_issues)
        if not any(
            issue.code in {"INVALID_TIME_VALUE", "INVALID_TIME_RANGE", "INVALID_STEP_SIZE"}
            for issue in timing_issues
        ):
            duration_ratio = (
                (config.stop_time - config.start_time) / config.communication_step
            )
            if not math.isclose(
                duration_ratio,
                round(duration_ratio),
                rel_tol=0.0,
                abs_tol=1e-9,
            ):
                issues.append(
                    ValidationIssue(
                        "communication_step",
                        "GRAPH_DURATION_NOT_COMMUNICATION_ALIGNED",
                        "graph duration 必须是 communication_step 的整数倍",
                    )
                )

        timing_valid = not issues
        node_ids = _node_id_indexes(graph.nodes)
        if not graph.nodes:
            issues.append(ValidationIssue("nodes", "EMPTY_GRAPH", "graph 必须至少包含一个 node"))

        metadata_by_index: dict[int, ModelMetadata] = {}
        config_valid_by_index: dict[int, bool] = {}
        effective_shapes_by_index: dict[int, dict[str, tuple[int, ...]]] = {}

        for index, node in enumerate(graph.nodes):
            node_field = f"nodes[{index}]"
            if _is_blank(node.node_id):
                issues.append(
                    ValidationIssue(
                        f"{node_field}.node_id", "EMPTY_NODE_ID", "node_id 不能为空"
                    )
                )
            elif len(node_ids[node.node_id]) > 1 and node_ids[node.node_id][0] != index:
                issues.append(
                    ValidationIssue(
                        f"{node_field}.node_id", "DUPLICATE_NODE_ID", "node_id 必须唯一"
                    )
                )
            if _is_blank(node.model_path):
                issues.append(
                    ValidationIssue(
                        f"{node_field}.model_path", "EMPTY_MODEL_PATH", "model_path 不能为空"
                    )
                )

        for index, node in enumerate(graph.nodes):
            if _is_blank(node.model_path):
                continue
            node_field = f"nodes[{index}]"
            try:
                metadata = self._importer.load(Path(node.model_path))
            except EngineError as error:
                issues.append(
                    ValidationIssue(
                        f"{node_field}.model_path", error.code.value, error.message
                    )
                )
                continue
            metadata_by_index[index] = metadata

        if timing_valid:
            for index, metadata in metadata_by_index.items():
                effective_config = build_node_simulation_config(
                    graph.nodes[index].config, config
                )
                node_report = validate_config(metadata, effective_config)
                issues.extend(_prefix_node_issues(index, node_report))
                config_valid_by_index[index] = node_report.is_valid
                if node_report.is_valid:
                    effective_shapes_by_index[index] = resolve_effective_shapes(
                        metadata, effective_config
                    )

        seen_connections: set[tuple[str, str, str, str]] = set()
        driven_targets: set[tuple[str, str]] = set()
        for index, connection in enumerate(graph.connections):
            _validate_connection(
                index,
                connection,
                graph,
                node_ids,
                metadata_by_index,
                config_valid_by_index,
                effective_shapes_by_index,
                seen_connections,
                driven_targets,
                issues,
            )

        return ValidationReport(tuple(issues))

def _validate_connection(
    index: int,
    connection: Connection,
    graph: SimulationGraph,
    node_ids: dict[str, list[int]],
    metadata_by_index: dict[int, ModelMetadata],
    config_valid_by_index: dict[int, bool],
    effective_shapes_by_index: dict[int, dict[str, tuple[int, ...]]],
    seen_connections: set[tuple[str, str, str, str]],
    driven_targets: set[tuple[str, str]],
    issues: list[ValidationIssue],
) -> None:
    source = connection.source
    target = connection.target
    connection_key = (
        source.node_id,
        source.variable_name,
        target.node_id,
        target.variable_name,
    )
    if connection_key in seen_connections:
        issues.append(
            ValidationIssue(
                f"connections[{index}]",
                "DUPLICATE_CONNECTION",
                "connection 不能重复声明",
            )
        )
        return
    seen_connections.add(connection_key)

    source_index = _unique_node_index(source, node_ids)
    target_index = _unique_node_index(target, node_ids)
    if source_index is None:
        if source.node_id not in node_ids:
            issues.append(
                ValidationIssue(
                    f"connections[{index}].source",
                    "UNKNOWN_SOURCE_NODE",
                    "connection source node 不存在",
                )
            )
    if target_index is None:
        if target.node_id not in node_ids:
            issues.append(
                ValidationIssue(
                    f"connections[{index}].target",
                    "UNKNOWN_TARGET_NODE",
                    "connection target node 不存在",
                )
            )
    if source_index is None or target_index is None:
        return

    source_metadata = metadata_by_index.get(source_index)
    target_metadata = metadata_by_index.get(target_index)
    if source_metadata is None or target_metadata is None:
        return
    source_variable = _find_variable(source_metadata, source.variable_name)
    target_variable = _find_variable(target_metadata, target.variable_name)
    if source_variable is None:
        issues.append(
            ValidationIssue(
                f"connections[{index}].source",
                "UNKNOWN_SOURCE_VARIABLE",
                "connection source variable 不存在",
            )
        )
    if target_variable is None:
        issues.append(
            ValidationIssue(
                f"connections[{index}].target",
                "UNKNOWN_TARGET_VARIABLE",
                "connection target variable 不存在",
            )
        )
    if source_variable is None or target_variable is None:
        return

    source_is_output = source_variable.causality == "output"
    target_is_input = target_variable.causality == "input"
    if not source_is_output:
        issues.append(
            ValidationIssue(
                f"connections[{index}].source",
                "INVALID_SOURCE_CAUSALITY",
                "connection source variable 必须是 output",
            )
        )
    if not target_is_input:
        issues.append(
            ValidationIssue(
                f"connections[{index}].target",
                "INVALID_TARGET_CAUSALITY",
                "connection target variable 必须是 input",
            )
        )
    if not source_is_output or not target_is_input:
        return

    target_key = (target.node_id, target.variable_name)
    if target_key in driven_targets:
        issues.append(
            ValidationIssue(
                f"connections[{index}].target",
                "MULTIPLE_INPUT_DRIVERS",
                "一个 target input 最多只能有一个 connection driver",
            )
        )
        return
    driven_targets.add(target_key)

    if config_valid_by_index.get(target_index, False) and _has_schedule_value(
        graph.nodes[target_index].config, target.variable_name
    ):
        issues.append(
            ValidationIssue(
                f"connections[{index}].target",
                "INPUT_SCHEDULE_CONNECTION_CONFLICT",
                "connection driver 与 target input_schedule 冲突",
            )
        )

    source_type = _canonical_connection_type(source_metadata, source_variable)
    target_type = _canonical_connection_type(target_metadata, target_variable)
    if source_type is None or target_type is None:
        issues.append(
            ValidationIssue(
                f"connections[{index}]",
                "UNSUPPORTED_CONNECTION_TYPE",
                "connection 不支持该 runtime data type",
            )
        )
        return
    if source_type != target_type:
        issues.append(
            ValidationIssue(
                f"connections[{index}]",
                "INCOMPATIBLE_CONNECTION_TYPE",
                "connection source 与 target data type 不兼容",
            )
        )
        return

    if not (
        config_valid_by_index.get(source_index, False)
        and config_valid_by_index.get(target_index, False)
    ):
        return
    source_shape = effective_shapes_by_index[source_index].get(
        source.variable_name, source_variable.shape
    )
    target_shape = effective_shapes_by_index[target_index].get(
        target.variable_name, target_variable.shape
    )
    if source_shape != target_shape:
        issues.append(
            ValidationIssue(
                f"connections[{index}]",
                "INCOMPATIBLE_CONNECTION_SHAPE",
                "connection source 与 target array shape 不兼容",
            )
        )


def build_node_simulation_config(
    node_config: ModelNodeConfig,
    graph_config: GraphSimulationConfig,
    *,
    selected_outputs: tuple[str, ...] | None = None,
) -> SimulationConfig:
    """Map graph-global and node-local settings to one runtime config."""
    return SimulationConfig(
        schema_version=graph_config.schema_version,
        start_time=graph_config.start_time,
        stop_time=graph_config.stop_time,
        communication_step=graph_config.communication_step,
        output_interval=graph_config.output_interval,
        relative_tolerance=node_config.relative_tolerance,
        parameters=node_config.parameters,
        initial_inputs=node_config.initial_inputs,
        selected_outputs=(
            node_config.selected_outputs
            if selected_outputs is None
            else selected_outputs
        ),
        input_schedule=node_config.input_schedule,
        execution_interface=node_config.execution_interface,
    )


def _prefix_node_issues(index: int, report: ValidationReport) -> tuple[ValidationIssue, ...]:
    prefix = f"nodes[{index}]"
    return tuple(
        ValidationIssue(
            f"{prefix}.model_path" if issue.field == "model" else f"{prefix}.config.{issue.field}",
            issue.code,
            issue.message,
        )
        for issue in report.issues
    )


def _node_id_indexes(nodes: tuple[ModelNode, ...]) -> dict[str, list[int]]:
    indexes: dict[str, list[int]] = {}
    for index, node in enumerate(nodes):
        if isinstance(node.node_id, str) and node.node_id.strip():
            indexes.setdefault(node.node_id, []).append(index)
    return indexes


def _unique_node_index(
    reference: PortReference, node_ids: dict[str, list[int]]
) -> int | None:
    indexes = node_ids.get(reference.node_id)
    if indexes is None or len(indexes) != 1:
        return None
    return indexes[0]


def _find_variable(
    metadata: ModelMetadata, variable_name: str
) -> VariableMetadata | None:
    return next(
        (variable for variable in metadata.variables if variable.name == variable_name),
        None,
    )


def _canonical_connection_type(
    metadata: ModelMetadata, variable: VariableMetadata
) -> str | None:
    data_type = variable.data_type.lower()
    if metadata.fmi_version == "2.0" and data_type == "real":
        return "float64"
    if metadata.fmi_version == "2.0" and data_type == "integer":
        return "int32"
    if data_type in {
        "float32", "float64", "int8", "uint8", "int16", "uint16",
        "int32", "uint32", "int64", "uint64", "boolean", "string",
        "enumeration",
    }:
        return data_type
    return None


def _has_schedule_value(config: ModelNodeConfig, variable_name: str) -> bool:
    return any(
        variable_name in update.values
        for update in config.input_schedule
        if hasattr(update, "values")
    )


def _is_blank(value: object) -> bool:
    return not isinstance(value, str) or not value.strip()
