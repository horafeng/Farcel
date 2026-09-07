"""Strict standard-JSON codec for portable project run artifacts."""

from __future__ import annotations

from collections.abc import Mapping
import json
import math
from typing import Any

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.graph import GraphSimulationResult
from farcel.contracts.models import SimulationState
from farcel.contracts.project_result import (
    PROJECT_RUN_ARTIFACT_SCHEMA_VERSION,
    ProjectAssetSnapshot,
    ProjectRunArtifact,
)
from farcel.infrastructure.project.json_repository import (
    _ProjectFormatError,
    _decode_case,
    _encode_case,
)


_ARTIFACT_FIELDS = (
    "schema_version",
    "run_id",
    "project_id",
    "case_snapshot",
    "asset_snapshots",
    "result",
)
_RESULT_FIELDS = (
    "start_time",
    "stop_time",
    "step_size",
    "completed_steps",
    "final_time",
    "completion_state",
    "timestamps",
    "node_outputs",
)
_FLOAT_TAG = "$farcel_float"
_FLOAT_VALUES = {
    "nan": float("nan"),
    "positive_infinity": float("inf"),
    "negative_infinity": float("-inf"),
}


class JsonProjectRunArtifactCodec:
    """Encode and decode one artifact without filesystem I/O."""

    def dumps(self, artifact: ProjectRunArtifact) -> str:
        try:
            document = _encode_artifact(artifact)
            return json.dumps(
                document, ensure_ascii=False, indent=2, allow_nan=False
            ) + "\n"
        except (AttributeError, TypeError, ValueError, _ProjectFormatError) as exc:
            raise _format_error("ProjectRunArtifact 无法编码", exc) from None

    def loads(self, text: str) -> ProjectRunArtifact:
        try:
            document = json.loads(text, parse_constant=_reject_json_constant)
        except (TypeError, json.JSONDecodeError, ValueError) as exc:
            raise _format_error("ProjectRunArtifact 不是有效标准 JSON", exc) from None
        try:
            return _decode_artifact(document)
        except (AttributeError, TypeError, ValueError, _ProjectFormatError) as exc:
            raise _format_error("ProjectRunArtifact 结构无效", exc) from None


def _encode_artifact(artifact: ProjectRunArtifact) -> dict[str, Any]:
    if artifact.schema_version != PROJECT_RUN_ARTIFACT_SCHEMA_VERSION:
        raise _ProjectFormatError("不支持的 project run artifact schema_version")
    return {
        "schema_version": _string(artifact.schema_version, "schema_version"),
        "run_id": _string(artifact.run_id, "run_id"),
        "project_id": _string(artifact.project_id, "project_id"),
        "case_snapshot": _encode_case(artifact.case_snapshot),
        "asset_snapshots": [_encode_asset(snapshot) for snapshot in artifact.asset_snapshots],
        "result": _encode_result(artifact.result),
    }


def _encode_asset(snapshot: ProjectAssetSnapshot) -> dict[str, str]:
    return {
        "asset_id": _string(snapshot.asset_id, "asset_id"),
        "relative_path": _string(snapshot.relative_path, "relative_path"),
        "sha256": _string(snapshot.sha256, "sha256"),
    }


def _encode_result(result: GraphSimulationResult) -> dict[str, Any]:
    if not isinstance(result.completion_state, SimulationState):
        raise _ProjectFormatError("completion_state 必须是 SimulationState")
    if not isinstance(result.node_outputs, Mapping):
        raise _ProjectFormatError("node_outputs 必须是 Mapping")
    return {
        "start_time": _finite_number(result.start_time, "start_time"),
        "stop_time": _finite_number(result.stop_time, "stop_time"),
        "step_size": _finite_number(result.step_size, "step_size"),
        "completed_steps": _integer(result.completed_steps, "completed_steps"),
        "final_time": _finite_number(result.final_time, "final_time"),
        "completion_state": result.completion_state.value,
        "timestamps": [_finite_number(value, "timestamps") for value in result.timestamps],
        "node_outputs": {
            _string(node_id, "node_outputs key"): _encode_node_outputs(outputs)
            for node_id, outputs in result.node_outputs.items()
        },
    }


def _encode_node_outputs(outputs: object) -> dict[str, list[Any]]:
    if not isinstance(outputs, Mapping):
        raise _ProjectFormatError("node outputs 必须是 Mapping")
    return {
        _string(name, "node output key"): [_encode_sample(value) for value in samples]
        for name, samples in outputs.items()
    }


def _encode_sample(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        if math.isnan(value):
            return {_FLOAT_TAG: "nan"}
        return {_FLOAT_TAG: "positive_infinity" if value > 0 else "negative_infinity"}
    if isinstance(value, (tuple, list)):
        return [_encode_sample(item) for item in value]
    if isinstance(value, Mapping):
        raise _ProjectFormatError("result sample 不支持 Mapping")
    raise _ProjectFormatError(f"不支持的 result sample 类型: {type(value).__name__}")


def _decode_artifact(document: object) -> ProjectRunArtifact:
    data = _object(document, "project run artifact", _ARTIFACT_FIELDS)
    schema_version = _string(data["schema_version"], "schema_version")
    if schema_version != PROJECT_RUN_ARTIFACT_SCHEMA_VERSION:
        raise _ProjectFormatError("不支持的 project run artifact schema_version")
    return ProjectRunArtifact(
        run_id=_string(data["run_id"], "run_id"),
        project_id=_string(data["project_id"], "project_id"),
        case_snapshot=_decode_case(data["case_snapshot"]),
        asset_snapshots=tuple(
            _decode_asset(item) for item in _array(data["asset_snapshots"], "asset_snapshots")
        ),
        result=_decode_result(data["result"]),
        schema_version=schema_version,
    )


def _decode_asset(document: object) -> ProjectAssetSnapshot:
    data = _object(document, "asset snapshot", ("asset_id", "relative_path", "sha256"))
    return ProjectAssetSnapshot(
        _string(data["asset_id"], "asset_id"),
        _string(data["relative_path"], "relative_path"),
        _string(data["sha256"], "sha256"),
    )


def _decode_result(document: object) -> GraphSimulationResult:
    data = _object(document, "result", _RESULT_FIELDS)
    try:
        state = SimulationState(_string(data["completion_state"], "completion_state"))
    except ValueError as exc:
        raise _ProjectFormatError("未知 completion_state") from exc
    return GraphSimulationResult(
        start_time=_finite_number(data["start_time"], "start_time"),
        stop_time=_finite_number(data["stop_time"], "stop_time"),
        step_size=_finite_number(data["step_size"], "step_size"),
        completed_steps=_integer(data["completed_steps"], "completed_steps"),
        final_time=_finite_number(data["final_time"], "final_time"),
        completion_state=state,
        timestamps=tuple(
            _finite_number(item, "timestamps") for item in _array(data["timestamps"], "timestamps")
        ),
        node_outputs=_decode_node_outputs(data["node_outputs"]),
    )


def _decode_node_outputs(document: object) -> dict[str, dict[str, tuple[Any, ...]]]:
    if not isinstance(document, dict):
        raise _ProjectFormatError("node_outputs 必须是 object")
    outputs: dict[str, dict[str, tuple[Any, ...]]] = {}
    for node_id, values in document.items():
        if not isinstance(values, dict):
            raise _ProjectFormatError("node outputs 必须是 object")
        outputs[_string(node_id, "node_outputs key")] = {
            _string(name, "node output key"): tuple(
                _decode_sample(item) for item in _array(samples, "samples")
            )
            for name, samples in values.items()
        }
    return outputs


def _decode_sample(value: object) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _ProjectFormatError("result sample 不能是裸非有限浮点数")
        return value
    if isinstance(value, list):
        return tuple(_decode_sample(item) for item in value)
    if isinstance(value, dict):
        if set(value) != {_FLOAT_TAG}:
            raise _ProjectFormatError("result sample object 必须是 Farcel float tag")
        tag = value[_FLOAT_TAG]
        if tag not in _FLOAT_VALUES:
            raise _ProjectFormatError("未知 Farcel float tag")
        return _FLOAT_VALUES[tag]
    raise _ProjectFormatError("不支持的 result sample JSON value")


def _object(document: object, name: str, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != set(fields):
        raise _ProjectFormatError(f"{name} 字段结构无效")
    return document


def _array(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise _ProjectFormatError(f"{name} 必须是 array")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise _ProjectFormatError(f"{name} 必须是字符串")
    return value


def _finite_number(value: object, name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _ProjectFormatError(f"{name} 必须是有限数值")
    return value


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _ProjectFormatError(f"{name} 必须是整数")
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"不允许 JSON constant {value}")


def _format_error(message: str, cause: Exception) -> EngineError:
    return EngineError(
        ErrorCode.PROJECT_FORMAT_ERROR,
        message,
        {"diagnostic": str(cause)},
    )
