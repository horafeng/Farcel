"""Canonical UTF-8 JSON persistence for declarative simulation projects."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any
from dataclasses import replace

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.graph import (
    Connection,
    GraphSimulationConfig,
    ModelNode,
    ModelNodeConfig,
    PortReference,
    SimulationGraph,
)
from farcel.contracts.models import InputUpdate, InterfaceType, SimulationState
from farcel.contracts.project import (
    LATEST_PROJECT_SCHEMA_VERSION,
    PROJECT_SCHEMA_VERSION,
    SUPPORTED_PROJECT_SCHEMA_VERSIONS,
    ModelAsset,
    ProjectSchemaMigrationCandidate,
    ProjectSchemaMigrationResult,
    ProjectRunRecord,
    SimulationCase,
    SimulationProject,
)
from farcel.contracts.modelica_provenance import (
    ModelicaBuildProvenance,
    ModelicaSourceSnapshot,
)


_PROJECT_FILENAME = "project.json"


class _ProjectFormatError(ValueError):
    pass


class LocalJsonProjectRepository:
    """Persist a SimulationProject as a canonical ``project.json`` document."""

    def load(self, project_root: Path) -> SimulationProject:
        project_path = Path(project_root) / _PROJECT_FILENAME
        return _read_project(project_path)

    def load_1_0_migration_candidate(
        self, project_root: Path
    ) -> ProjectSchemaMigrationCandidate:
        project_path = Path(project_root) / _PROJECT_FILENAME
        project, document_bytes = _read_project_with_bytes(project_path)
        if project.schema_version != PROJECT_SCHEMA_VERSION:
            raise _format_error("仅可迁移 Project schema 1.0", project_path, ValueError(project.schema_version))
        return ProjectSchemaMigrationCandidate(project, _sha256_bytes(document_bytes))

    def migrate_1_0_to_1_1(
        self,
        project_root: Path,
        candidate: ProjectSchemaMigrationCandidate,
    ) -> ProjectSchemaMigrationResult:
        project_path = Path(project_root) / _PROJECT_FILENAME
        if (
            not isinstance(candidate, ProjectSchemaMigrationCandidate)
            or candidate.project.schema_version != PROJECT_SCHEMA_VERSION
            or candidate.project.modelica_build_provenance
        ):
            raise _format_error("Project schema 迁移候选无效", project_path, ValueError("candidate"))
        current, original_bytes = _read_project_with_bytes(project_path)
        if current != candidate.project or _sha256_bytes(original_bytes) != candidate.document_sha256:
            raise _io_error("迁移期间 project.json 已被修改，拒绝覆盖", project_path, ValueError("conflict"))

        migrated = replace(
            current,
            schema_version=LATEST_PROJECT_SCHEMA_VERSION,
            modelica_build_provenance=(),
        )
        try:
            document = _encode_project(migrated)
            text = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            if _decode_project(json.loads(text)) != migrated:
                raise _ProjectFormatError("迁移后的 project.json 自检失败")
        except (TypeError, ValueError, _ProjectFormatError) as exc:
            raise _format_error("无法构造 schema 1.1 project.json", project_path, exc) from None

        backup_path = project_path.with_name(
            f"{_PROJECT_FILENAME}.1.0-backup-{candidate.document_sha256}.json"
        )
        _write_backup_without_overwrite(backup_path, original_bytes, project_path)
        _replace_if_unchanged(project_path, text, candidate.document_sha256)
        return ProjectSchemaMigrationResult(migrated, backup_path.name)

    def save(self, project_root: Path, project: SimulationProject) -> None:
        project_path = Path(project_root) / _PROJECT_FILENAME
        try:
            document = _encode_project(project)
            text = json.dumps(
                document,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ) + "\n"
        except (AttributeError, TypeError, ValueError, _ProjectFormatError) as exc:
            raise _format_error("SimulationProject 无法编码为 project.json", project_path, exc) from None

        _replace_project_text(project_path, text)


def _read_project(project_path: Path) -> SimulationProject:
    return _read_project_with_bytes(project_path)[0]


def _read_project_with_bytes(project_path: Path) -> tuple[SimulationProject, bytes]:
    try:
        document_bytes = project_path.read_bytes()
        text = document_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _format_error("project.json 不是有效 UTF-8", project_path, exc) from None
    except OSError as exc:
        raise _io_error("无法读取 project.json", project_path, exc) from None

    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _format_error("project.json 不是有效 JSON", project_path, exc) from None

    try:
        return _decode_project(document), document_bytes
    except _ProjectFormatError as exc:
        raise _format_error("project.json 结构无效", project_path, exc) from None


def _replace_project_text(project_path: Path, text: str) -> None:
    temporary_path: Path | None = None
    try:
        project_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=project_path.parent,
            prefix=".project-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, project_path)
        temporary_path = None
    except OSError as exc:
        raise _io_error("无法原子保存 project.json", project_path, exc) from None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _write_backup_without_overwrite(
    backup_path: Path,
    original_bytes: bytes,
    project_path: Path,
) -> None:
    try:
        with backup_path.open("xb") as stream:
            stream.write(original_bytes)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        try:
            if backup_path.read_bytes() == original_bytes:
                return
        except OSError as exc:
            raise _io_error("无法验证既有迁移备份", project_path, exc) from None
        raise _io_error("迁移备份路径已被不同内容占用", project_path, ValueError(backup_path.name))
    except OSError as exc:
        raise _io_error("无法创建 project.json 迁移备份", project_path, exc) from None


def _replace_if_unchanged(project_path: Path, text: str, expected_sha256: str) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=project_path.parent,
            prefix=".project-migration-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if _sha256_bytes(project_path.read_bytes()) != expected_sha256:
            raise _io_error("迁移期间 project.json 已被修改，拒绝覆盖", project_path, ValueError("conflict"))
        os.replace(temporary_path, project_path)
        temporary_path = None
    except EngineError:
        raise
    except OSError as exc:
        raise _io_error("无法发布迁移后的 project.json", project_path, exc) from None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _encode_project(project: SimulationProject) -> dict[str, Any]:
    if project.schema_version not in SUPPORTED_PROJECT_SCHEMA_VERSIONS:
        raise _ProjectFormatError("不支持的 project schema_version")
    if project.schema_version == PROJECT_SCHEMA_VERSION and project.modelica_build_provenance:
        raise _ProjectFormatError("schema 1.0 不允许保存 Modelica provenance")
    if project.schema_version == LATEST_PROJECT_SCHEMA_VERSION:
        _validate_provenance_persistence_links(project)
    document: dict[str, Any] = {
        "schema_version": project.schema_version,
        "project_id": _encode_string(project.project_id, "project_id"),
        "name": _encode_string(project.name, "name"),
        "model_assets": [_encode_asset(asset) for asset in project.model_assets],
        "simulation_cases": [_encode_case(case) for case in project.simulation_cases],
        "run_history": [_encode_run_record(record) for record in project.run_history],
    }
    if project.schema_version == LATEST_PROJECT_SCHEMA_VERSION:
        document["modelica_build_provenance"] = [
            _encode_provenance(provenance)
            for provenance in project.modelica_build_provenance
        ]
    return document


def _validate_provenance_persistence_links(project: SimulationProject) -> None:
    """Keep schema 1.1 relation errors out of persisted JSON without I/O."""

    asset_counts: dict[str, int] = {}
    for asset in project.model_assets:
        asset_counts[asset.asset_id] = asset_counts.get(asset.asset_id, 0) + 1
    build_ids: set[str] = set()
    referenced_assets: set[str] = set()
    for provenance in project.modelica_build_provenance:
        if not isinstance(provenance, ModelicaBuildProvenance):
            raise _ProjectFormatError("modelica_build_provenance 必须是 ModelicaBuildProvenance")
        if provenance.build_id in build_ids:
            raise _ProjectFormatError("modelica_build_provenance.build_id 必须唯一")
        build_ids.add(provenance.build_id)
        if asset_counts.get(provenance.fmu_asset_id) != 1:
            raise _ProjectFormatError("fmu_asset_id 必须唯一关联一个 ModelAsset")
        if provenance.fmu_asset_id in referenced_assets:
            raise _ProjectFormatError("同一 ModelAsset 不能关联多条 Modelica provenance")
        referenced_assets.add(provenance.fmu_asset_id)
        asset = next(asset for asset in project.model_assets if asset.asset_id == provenance.fmu_asset_id)
        prefix = f"modelica-builds/build-{provenance.build_id}/"
        if (
            provenance.source_snapshot.relative_path != f"{prefix}source/model.mo"
            or asset.relative_path != f"{prefix}model/model.fmu"
        ):
            raise _ProjectFormatError("Modelica provenance 与 ModelAsset 不属于同一构建包")


def _encode_provenance(provenance: ModelicaBuildProvenance) -> dict[str, Any]:
    if not isinstance(provenance, ModelicaBuildProvenance):
        raise _ProjectFormatError("modelica_build_provenance 必须是 ModelicaBuildProvenance")
    return {
        "build_id": _encode_string(provenance.build_id, "build_id"),
        "source_snapshot": {
            "relative_path": _encode_string(
                provenance.source_snapshot.relative_path,
                "source_snapshot.relative_path",
            ),
            "sha256": _encode_string(provenance.source_snapshot.sha256, "source_snapshot.sha256"),
        },
        "model_name": _encode_string(provenance.model_name, "model_name"),
        "compiler_name": _encode_string(provenance.compiler_name, "compiler_name"),
        "compiler_version": _encode_string(provenance.compiler_version, "compiler_version"),
        "fmi_version": _encode_string(provenance.fmi_version, "fmi_version"),
        "fmu_type": _encode_string(provenance.fmu_type, "fmu_type"),
        "fmu_asset_id": _encode_string(provenance.fmu_asset_id, "fmu_asset_id"),
    }


def _encode_asset(asset: ModelAsset) -> dict[str, str]:
    return {
        "asset_id": _encode_string(asset.asset_id, "asset_id"),
        "display_name": _encode_string(asset.display_name, "display_name"),
        "relative_path": _encode_string(asset.relative_path, "relative_path"),
        "sha256": _encode_string(asset.sha256, "sha256"),
    }


def _encode_case(case: SimulationCase) -> dict[str, Any]:
    return {
        "case_id": _encode_string(case.case_id, "case_id"),
        "name": _encode_string(case.name, "name"),
        "graph": _encode_graph(case.graph),
        "config": _encode_graph_config(case.config),
    }


def _encode_graph(graph: SimulationGraph) -> dict[str, Any]:
    return {
        "nodes": [_encode_node(node) for node in graph.nodes],
        "connections": [_encode_connection(connection) for connection in graph.connections],
    }


def _encode_node(node: ModelNode) -> dict[str, Any]:
    return {
        "node_id": _encode_string(node.node_id, "node_id"),
        "model_path": _encode_string(node.model_path, "model_path"),
        "config": _encode_node_config(node.config),
    }


def _encode_node_config(config: ModelNodeConfig) -> dict[str, Any]:
    if config.execution_interface is not None and not isinstance(
        config.execution_interface, InterfaceType
    ):
        raise _ProjectFormatError("execution_interface 必须是 InterfaceType 或 None")
    return {
        "parameters": _encode_value_mapping(config.parameters),
        "initial_inputs": _encode_value_mapping(config.initial_inputs),
        "input_schedule": [_encode_input_update(update) for update in config.input_schedule],
        "selected_outputs": [_encode_string(value, "selected_outputs") for value in config.selected_outputs],
        "relative_tolerance": _encode_optional_number(
            config.relative_tolerance, "relative_tolerance"
        ),
        "execution_interface": (
            None
            if config.execution_interface is None
            else config.execution_interface.value
        ),
    }


def _encode_input_update(update: InputUpdate) -> dict[str, Any]:
    return {
        "time": _encode_number(update.time, "input_schedule.time"),
        "values": _encode_value_mapping(update.values),
    }


def _encode_connection(connection: Connection) -> dict[str, Any]:
    return {
        "source": _encode_port_reference(connection.source),
        "target": _encode_port_reference(connection.target),
    }


def _encode_port_reference(reference: PortReference) -> dict[str, str]:
    return {
        "node_id": _encode_string(reference.node_id, "port.node_id"),
        "variable_name": _encode_string(reference.variable_name, "port.variable_name"),
    }


def _encode_graph_config(config: GraphSimulationConfig) -> dict[str, Any]:
    return {
        "schema_version": _encode_string(config.schema_version, "graph.schema_version"),
        "start_time": _encode_number(config.start_time, "graph.start_time"),
        "stop_time": _encode_number(config.stop_time, "graph.stop_time"),
        "communication_step": _encode_number(
            config.communication_step, "graph.communication_step"
        ),
        "output_interval": _encode_optional_number(
            config.output_interval, "graph.output_interval"
        ),
    }


def _encode_run_record(record: ProjectRunRecord) -> dict[str, Any]:
    if not isinstance(record.completion_state, SimulationState):
        raise _ProjectFormatError("completion_state 必须是 SimulationState")
    return {
        "run_id": _encode_string(record.run_id, "run_id"),
        "case_id": _encode_string(record.case_id, "case_id"),
        "completion_state": record.completion_state.value,
        "final_time": _encode_number(record.final_time, "final_time"),
        "completed_steps": _encode_integer(record.completed_steps, "completed_steps"),
        "result_path": _encode_string(record.result_path, "result_path"),
    }


def _encode_value_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(values, Mapping):
        raise _ProjectFormatError("配置 mapping 必须是 Mapping")
    return {
        _encode_string(key, "mapping key"): _encode_value(value)
        for key, value in values.items()
    }


def _encode_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _ProjectFormatError("generic value 不能为非有限浮点数")
        return value
    if isinstance(value, (tuple, list)):
        return [_encode_value(item) for item in value]
    if isinstance(value, Mapping):
        return _encode_value_mapping(value)
    raise _ProjectFormatError(f"不支持的 generic value 类型: {type(value).__name__}")


def _encode_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise _ProjectFormatError(f"{field_name} 必须是字符串")
    return value


def _encode_number(value: object, field_name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _ProjectFormatError(f"{field_name} 必须是有限数值")
    if not math.isfinite(value):
        raise _ProjectFormatError(f"{field_name} 不能为非有限数值")
    return value


def _encode_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _ProjectFormatError(f"{field_name} 必须是整数")
    return value


def _encode_optional_number(value: object, field_name: str) -> int | float | None:
    return None if value is None else _encode_number(value, field_name)


def _decode_project(document: object) -> SimulationProject:
    if not isinstance(document, dict):
        raise _ProjectFormatError("project 必须是 object")
    schema_version = _string(document.get("schema_version"), "schema_version")
    expected_fields = (
        (
            "schema_version",
            "project_id",
            "name",
            "model_assets",
            "simulation_cases",
            "run_history",
        )
        if schema_version == PROJECT_SCHEMA_VERSION
        else (
            "schema_version",
            "project_id",
            "name",
            "model_assets",
            "simulation_cases",
            "run_history",
            "modelica_build_provenance",
        )
    )
    data = _object_with_fields(
        document,
        "project",
        expected_fields,
    )
    if schema_version not in SUPPORTED_PROJECT_SCHEMA_VERSIONS:
        raise _ProjectFormatError("不支持的 project schema_version")
    return SimulationProject(
        project_id=_string(data["project_id"], "project_id"),
        name=_string(data["name"], "name"),
        model_assets=tuple(_decode_asset(item) for item in _array(data["model_assets"], "model_assets")),
        simulation_cases=tuple(
            _decode_case(item) for item in _array(data["simulation_cases"], "simulation_cases")
        ),
        run_history=tuple(
            _decode_run_record(item) for item in _array(data["run_history"], "run_history")
        ),
        schema_version=schema_version,
        modelica_build_provenance=(
            ()
            if schema_version == PROJECT_SCHEMA_VERSION
            else tuple(
                _decode_provenance(item)
                for item in _array(
                    data["modelica_build_provenance"],
                    "modelica_build_provenance",
                )
            )
        ),
    )


def _decode_provenance(document: object) -> ModelicaBuildProvenance:
    data = _object_with_fields(
        document,
        "modelica provenance",
        (
            "build_id",
            "source_snapshot",
            "model_name",
            "compiler_name",
            "compiler_version",
            "fmi_version",
            "fmu_type",
            "fmu_asset_id",
        ),
    )
    source = _object_with_fields(
        data["source_snapshot"],
        "source_snapshot",
        ("relative_path", "sha256"),
    )
    return ModelicaBuildProvenance(
        build_id=_string(data["build_id"], "build_id"),
        source_snapshot=ModelicaSourceSnapshot(
            relative_path=_string(source["relative_path"], "source_snapshot.relative_path"),
            sha256=_string(source["sha256"], "source_snapshot.sha256"),
        ),
        model_name=_string(data["model_name"], "model_name"),
        compiler_name=_string(data["compiler_name"], "compiler_name"),
        compiler_version=_string(data["compiler_version"], "compiler_version"),
        fmi_version=_string(data["fmi_version"], "fmi_version"),
        fmu_type=_string(data["fmu_type"], "fmu_type"),
        fmu_asset_id=_string(data["fmu_asset_id"], "fmu_asset_id"),
    )


def _decode_asset(document: object) -> ModelAsset:
    data = _object_with_fields(
        document, "model_asset", ("asset_id", "display_name", "relative_path", "sha256")
    )
    return ModelAsset(
        asset_id=_string(data["asset_id"], "asset_id"),
        display_name=_string(data["display_name"], "display_name"),
        relative_path=_string(data["relative_path"], "relative_path"),
        sha256=_string(data["sha256"], "sha256"),
    )


def _decode_case(document: object) -> SimulationCase:
    data = _object_with_fields(document, "simulation_case", ("case_id", "name", "graph", "config"))
    return SimulationCase(
        case_id=_string(data["case_id"], "case_id"),
        name=_string(data["name"], "name"),
        graph=_decode_graph(data["graph"]),
        config=_decode_graph_config(data["config"]),
    )


def _decode_graph(document: object) -> SimulationGraph:
    data = _object_with_fields(document, "graph", ("nodes", "connections"))
    return SimulationGraph(
        nodes=tuple(_decode_node(item) for item in _array(data["nodes"], "graph.nodes")),
        connections=tuple(
            _decode_connection(item) for item in _array(data["connections"], "graph.connections")
        ),
    )


def _decode_node(document: object) -> ModelNode:
    data = _object_with_fields(document, "node", ("node_id", "model_path", "config"))
    return ModelNode(
        node_id=_string(data["node_id"], "node_id"),
        model_path=_string(data["model_path"], "model_path"),
        config=_decode_node_config(data["config"]),
    )


def _decode_node_config(document: object) -> ModelNodeConfig:
    data = _object_with_fields(
        document,
        "node config",
        (
            "parameters",
            "initial_inputs",
            "input_schedule",
            "selected_outputs",
            "relative_tolerance",
            "execution_interface",
        ),
    )
    interface_value = data["execution_interface"]
    if interface_value is None:
        interface = None
    else:
        try:
            interface = InterfaceType(_string(interface_value, "execution_interface"))
        except ValueError as exc:
            raise _ProjectFormatError("未知 execution_interface") from exc
    return ModelNodeConfig(
        parameters=_decode_value_mapping(data["parameters"], "parameters"),
        initial_inputs=_decode_value_mapping(data["initial_inputs"], "initial_inputs"),
        input_schedule=tuple(
            _decode_input_update(item)
            for item in _array(data["input_schedule"], "input_schedule")
        ),
        selected_outputs=tuple(
            _string(item, "selected_outputs")
            for item in _array(data["selected_outputs"], "selected_outputs")
        ),
        relative_tolerance=_optional_number(data["relative_tolerance"], "relative_tolerance"),
        execution_interface=interface,
    )


def _decode_input_update(document: object) -> InputUpdate:
    data = _object_with_fields(document, "input update", ("time", "values"))
    return InputUpdate(
        time=_number(data["time"], "input_schedule.time"),
        values=_decode_value_mapping(data["values"], "input_schedule.values"),
    )


def _decode_connection(document: object) -> Connection:
    data = _object_with_fields(document, "connection", ("source", "target"))
    return Connection(
        source=_decode_port_reference(data["source"]),
        target=_decode_port_reference(data["target"]),
    )


def _decode_port_reference(document: object) -> PortReference:
    data = _object_with_fields(document, "port reference", ("node_id", "variable_name"))
    return PortReference(
        node_id=_string(data["node_id"], "port.node_id"),
        variable_name=_string(data["variable_name"], "port.variable_name"),
    )


def _decode_graph_config(document: object) -> GraphSimulationConfig:
    data = _object_with_fields(
        document,
        "graph config",
        ("schema_version", "start_time", "stop_time", "communication_step", "output_interval"),
    )
    return GraphSimulationConfig(
        schema_version=_string(data["schema_version"], "graph.schema_version"),
        start_time=_number(data["start_time"], "graph.start_time"),
        stop_time=_number(data["stop_time"], "graph.stop_time"),
        communication_step=_number(data["communication_step"], "graph.communication_step"),
        output_interval=_optional_number(data["output_interval"], "graph.output_interval"),
    )


def _decode_run_record(document: object) -> ProjectRunRecord:
    data = _object_with_fields(
        document,
        "run record",
        ("run_id", "case_id", "completion_state", "final_time", "completed_steps", "result_path"),
    )
    try:
        state = SimulationState(_string(data["completion_state"], "completion_state"))
    except ValueError as exc:
        raise _ProjectFormatError("未知 completion_state") from exc
    return ProjectRunRecord(
        run_id=_string(data["run_id"], "run_id"),
        case_id=_string(data["case_id"], "case_id"),
        completion_state=state,
        final_time=_number(data["final_time"], "final_time"),
        completed_steps=_integer(data["completed_steps"], "completed_steps"),
        result_path=_string(data["result_path"], "result_path"),
    )


def _decode_value_mapping(document: object, field_name: str) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise _ProjectFormatError(f"{field_name} 必须是 object")
    return {
        _string(key, f"{field_name} key"): _decode_value(value)
        for key, value in document.items()
    }


def _decode_value(value: object) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _ProjectFormatError("generic value 不能为非有限浮点数")
        return value
    if isinstance(value, list):
        return tuple(_decode_value(item) for item in value)
    if isinstance(value, dict):
        return _decode_value_mapping(value, "generic mapping")
    raise _ProjectFormatError("不支持的 generic JSON value")


def _object_with_fields(
    document: object, field_name: str, expected_fields: tuple[str, ...]
) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != set(expected_fields):
        raise _ProjectFormatError(f"{field_name} 字段结构无效")
    return document


def _array(value: object, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise _ProjectFormatError(f"{field_name} 必须是 array")
    return value


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise _ProjectFormatError(f"{field_name} 必须是字符串")
    return value


def _number(value: object, field_name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _ProjectFormatError(f"{field_name} 必须是有限数值")
    if not math.isfinite(value):
        raise _ProjectFormatError(f"{field_name} 不能为非有限数值")
    return value


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _ProjectFormatError(f"{field_name} 必须是整数")
    return value


def _optional_number(value: object, field_name: str) -> int | float | None:
    return None if value is None else _number(value, field_name)


def _format_error(message: str, project_path: Path, cause: Exception) -> EngineError:
    return EngineError(
        ErrorCode.PROJECT_FORMAT_ERROR,
        message,
        {"path": str(project_path), "diagnostic": str(cause)},
    )


def _io_error(message: str, project_path: Path, cause: Exception) -> EngineError:
    return EngineError(
        ErrorCode.PROJECT_IO_ERROR,
        message,
        {"path": str(project_path), "diagnostic": str(cause)},
    )
