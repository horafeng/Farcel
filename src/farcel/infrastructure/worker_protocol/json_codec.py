"""严格 UTF-8 standard-JSON Worker protocol codec。"""

from __future__ import annotations

from collections.abc import Mapping
import json
import math
from typing import Any

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import InputUpdate, InterfaceType, SimulationConfig
from farcel.contracts.ports import WorkerProtocolSemanticValidator
from farcel.contracts.worker_protocol import (
    AdvanceToRequest, CreateRuntimeRequest, CreateRuntimeResponse,
    HasAssetRequest, HasAssetResponse, PutAssetRequest, PutAssetResponse,
    ReadOutputsResponse, RemoteError, RuntimeAck, RuntimeCommand,
    SetInputsRequest, WorkerMessageType, WorkerRequest, WorkerResponse,
)


_REQUEST_FIELDS = ("protocol_version", "request_id", "worker_id", "message_type", "payload")
_RESPONSE_FIELDS = ("protocol_version", "request_id", "worker_id", "message_type", "ok", "payload", "error")
_CONFIG_FIELDS = ("schema_version", "start_time", "stop_time", "communication_step", "output_interval", "relative_tolerance", "parameters", "initial_inputs", "selected_outputs", "input_schedule", "execution_interface")
_FLOAT_FIELDS = ("$farcel_type", "value")
_MAPPING_FIELDS = ("$farcel_type", "entries")


class JsonWorkerProtocolCodec:
    """仅在 DTO 与 bytes 间转换；不执行网络传输。"""

    def __init__(self, validator: WorkerProtocolSemanticValidator) -> None:
        self._validator = validator

    def encode_request(self, request: WorkerRequest) -> bytes:
        try:
            _require_valid(self._validator.validate_request(request))
            return _dumps(_encode_request(request))
        except EngineError:
            raise
        except (AttributeError, TypeError, ValueError) as exc:
            raise _error("worker_protocol_encode", "Worker request 无法编码", exc) from None

    def decode_request(self, data: bytes) -> WorkerRequest:
        try:
            request = _decode_request(_loads(data))
            _require_valid(self._validator.validate_request(request))
            return request
        except EngineError:
            raise
        except (AttributeError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _error("worker_protocol_decode", "Worker request 无效", exc) from None

    def encode_response(self, response: WorkerResponse) -> bytes:
        try:
            _require_valid(self._validator.validate_response(response))
            return _dumps(_encode_response(response))
        except EngineError:
            raise
        except (AttributeError, TypeError, ValueError) as exc:
            raise _error("worker_protocol_encode", "Worker response 无法编码", exc) from None

    def decode_response(self, data: bytes) -> WorkerResponse:
        try:
            response = _decode_response(_loads(data))
            _require_valid(self._validator.validate_response(response))
            return response
        except EngineError:
            raise
        except (AttributeError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _error("worker_protocol_decode", "Worker response 无效", exc) from None


def _encode_request(request: WorkerRequest) -> dict[str, Any]:
    return {"protocol_version": request.protocol_version, "request_id": request.request_id, "worker_id": request.worker_id, "message_type": request.message_type.value, "payload": _encode_request_payload(request.message_type, request.payload)}


def _encode_response(response: WorkerResponse) -> dict[str, Any]:
    return {"protocol_version": response.protocol_version, "request_id": response.request_id, "worker_id": response.worker_id, "message_type": response.message_type.value, "ok": response.ok, "payload": _encode_response_payload(response.message_type, response.payload) if response.ok else None, "error": _encode_error(response.error) if response.error is not None else None}


def _decode_request(document: object) -> WorkerRequest:
    data = _object(document, "request", _REQUEST_FIELDS)
    message_type = _message_type(data["message_type"])
    return WorkerRequest(_string(data["request_id"], "request_id"), _string(data["worker_id"], "worker_id"), message_type, _decode_request_payload(message_type, data["payload"]), _string(data["protocol_version"], "protocol_version"))


def _decode_response(document: object) -> WorkerResponse:
    data = _object(document, "response", _RESPONSE_FIELDS)
    message_type = _message_type(data["message_type"])
    ok = data["ok"]
    if not isinstance(ok, bool):
        raise ValueError("ok 必须是 bool")
    error = _decode_error(data["error"])
    payload = _decode_response_payload(message_type, data["payload"]) if ok else data["payload"]
    return WorkerResponse(_string(data["request_id"], "request_id"), _string(data["worker_id"], "worker_id"), message_type, ok, payload, error, _string(data["protocol_version"], "protocol_version"))


def _encode_request_payload(message_type: WorkerMessageType, payload: object | None) -> object:
    if message_type in {WorkerMessageType.HELLO, WorkerMessageType.PING}: return None
    if isinstance(payload, HasAssetRequest): return {"sha256": payload.sha256}
    if isinstance(payload, PutAssetRequest): return {"sha256": payload.sha256, "size": payload.size}
    if isinstance(payload, CreateRuntimeRequest): return {"node_id": payload.node_id, "asset_sha256": payload.asset_sha256, "config": _encode_config(payload.config)}
    if isinstance(payload, RuntimeCommand): return {"runtime_id": payload.runtime_id}
    if isinstance(payload, SetInputsRequest): return {"runtime_id": payload.runtime_id, "values": _encode_value(payload.values)}
    if isinstance(payload, AdvanceToRequest): return {"runtime_id": payload.runtime_id, "target_time": payload.target_time}
    raise ValueError("未知 request payload")


def _decode_request_payload(message_type: WorkerMessageType, payload: object) -> object | None:
    if message_type in {WorkerMessageType.HELLO, WorkerMessageType.PING}:
        if payload is not None: raise ValueError("HELLO/PING payload 必须为 null")
        return None
    if message_type is WorkerMessageType.HAS_ASSET: return HasAssetRequest(_object(payload, "has_asset", ("sha256",))["sha256"])
    if message_type is WorkerMessageType.PUT_ASSET:
        data = _object(payload, "put_asset", ("sha256", "size")); return PutAssetRequest(data["sha256"], data["size"])
    if message_type is WorkerMessageType.CREATE_RUNTIME:
        data = _object(payload, "create_runtime", ("node_id", "asset_sha256", "config")); return CreateRuntimeRequest(data["node_id"], data["asset_sha256"], _decode_config(data["config"]))
    if message_type in {WorkerMessageType.INITIALIZE, WorkerMessageType.READ_OUTPUTS, WorkerMessageType.TERMINATE, WorkerMessageType.CLOSE}: return RuntimeCommand(_object(payload, "runtime command", ("runtime_id",))["runtime_id"])
    if message_type is WorkerMessageType.SET_INPUTS:
        data = _object(payload, "set_inputs", ("runtime_id", "values")); return SetInputsRequest(data["runtime_id"], _decode_mapping(data["values"], "values"))
    if message_type is WorkerMessageType.ADVANCE_TO:
        data = _object(payload, "advance_to", ("runtime_id", "target_time")); return AdvanceToRequest(data["runtime_id"], data["target_time"])
    raise ValueError("未知 request message_type")


def _encode_response_payload(message_type: WorkerMessageType, payload: object | None) -> object:
    if message_type in {WorkerMessageType.HELLO, WorkerMessageType.PING}: return None
    if isinstance(payload, HasAssetResponse): return {"sha256": payload.sha256, "available": payload.available}
    if isinstance(payload, PutAssetResponse): return {"sha256": payload.sha256}
    if isinstance(payload, CreateRuntimeResponse): return {"runtime_id": payload.runtime_id}
    if isinstance(payload, RuntimeAck): return {"runtime_id": payload.runtime_id}
    if isinstance(payload, ReadOutputsResponse): return {"runtime_id": payload.runtime_id, "outputs": _encode_value(payload.outputs)}
    raise ValueError("未知 response payload")


def _decode_response_payload(message_type: WorkerMessageType, payload: object) -> object | None:
    if message_type in {WorkerMessageType.HELLO, WorkerMessageType.PING}:
        if payload is not None: raise ValueError("HELLO/PING payload 必须为 null")
        return None
    if message_type is WorkerMessageType.HAS_ASSET:
        data = _object(payload, "has_asset response", ("sha256", "available")); return HasAssetResponse(data["sha256"], data["available"])
    if message_type is WorkerMessageType.PUT_ASSET: return PutAssetResponse(_object(payload, "put_asset response", ("sha256",))["sha256"])
    if message_type is WorkerMessageType.CREATE_RUNTIME: return CreateRuntimeResponse(_object(payload, "create_runtime response", ("runtime_id",))["runtime_id"])
    if message_type in {WorkerMessageType.INITIALIZE, WorkerMessageType.SET_INPUTS, WorkerMessageType.ADVANCE_TO, WorkerMessageType.TERMINATE, WorkerMessageType.CLOSE}: return RuntimeAck(_object(payload, "runtime ack", ("runtime_id",))["runtime_id"])
    if message_type is WorkerMessageType.READ_OUTPUTS:
        data = _object(payload, "read_outputs response", ("runtime_id", "outputs")); return ReadOutputsResponse(data["runtime_id"], _decode_mapping(data["outputs"], "outputs"))
    raise ValueError("未知 response message_type")


def _encode_config(config: SimulationConfig) -> dict[str, Any]:
    return {"schema_version": config.schema_version, "start_time": config.start_time, "stop_time": config.stop_time, "communication_step": config.communication_step, "output_interval": config.output_interval, "relative_tolerance": config.relative_tolerance, "parameters": _encode_value(config.parameters), "initial_inputs": _encode_value(config.initial_inputs), "selected_outputs": list(config.selected_outputs), "input_schedule": [{"time": item.time, "values": _encode_value(item.values)} for item in config.input_schedule], "execution_interface": config.execution_interface.value if config.execution_interface is not None else None}


def _decode_config(document: object) -> SimulationConfig:
    data = _object(document, "SimulationConfig", _CONFIG_FIELDS)
    interface_value = data["execution_interface"]
    if interface_value is None: interface = None
    else:
        try: interface = InterfaceType(_string(interface_value, "execution_interface"))
        except ValueError as exc: raise ValueError("未知 execution_interface") from exc
    return SimulationConfig(schema_version=_string(data["schema_version"], "schema_version"), start_time=_number(data["start_time"], "start_time"), stop_time=_number(data["stop_time"], "stop_time"), communication_step=_number(data["communication_step"], "communication_step"), output_interval=_optional_number(data["output_interval"], "output_interval"), relative_tolerance=_optional_number(data["relative_tolerance"], "relative_tolerance"), parameters=_decode_mapping(data["parameters"], "parameters"), initial_inputs=_decode_mapping(data["initial_inputs"], "initial_inputs"), selected_outputs=tuple(_string(item, "selected_outputs") for item in _array(data["selected_outputs"], "selected_outputs")), input_schedule=tuple(_decode_input(item) for item in _array(data["input_schedule"], "input_schedule")), execution_interface=interface)


def _decode_input(document: object) -> InputUpdate:
    data = _object(document, "input update", ("time", "values")); return InputUpdate(_number(data["time"], "input_schedule.time"), _decode_mapping(data["values"], "input_schedule.values"))


def _encode_error(error: RemoteError) -> dict[str, Any]: return {"code": error.code.value, "message": error.message, "details": _encode_value(error.details)}
def _decode_error(document: object) -> RemoteError | None:
    if document is None: return None
    data = _object(document, "error", ("code", "message", "details"))
    try: code = ErrorCode(_string(data["code"], "error.code"))
    except ValueError as exc: raise ValueError("未知 ErrorCode") from exc
    return RemoteError(code, _string(data["message"], "error.message"), _decode_mapping(data["details"], "error.details"))


def _encode_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)): return value
    if isinstance(value, float):
        if math.isfinite(value): return value
        return {"$farcel_type": "float", "value": "nan" if math.isnan(value) else "positive_infinity" if value > 0 else "negative_infinity"}
    if isinstance(value, (tuple, list)): return [_encode_value(item) for item in value]
    if isinstance(value, Mapping):
        return {"$farcel_type": "mapping", "entries": [[_string(key, "mapping key"), _encode_value(item)] for key, item in value.items()]}
    raise ValueError(f"不支持的 generic value 类型: {type(value).__name__}")


def _decode_value(value: object) -> Any:
    if value is None or isinstance(value, (str, bool, int)): return value
    if isinstance(value, float):
        if not math.isfinite(value): raise ValueError("不允许裸非有限浮点数")
        return value
    if isinstance(value, list): return tuple(_decode_value(item) for item in value)
    if isinstance(value, dict):
        tag = value.get("$farcel_type")
        if tag == "float":
            data = _object(value, "float tag", _FLOAT_FIELDS); tags = {"nan": float("nan"), "positive_infinity": float("inf"), "negative_infinity": float("-inf")}
            if data["value"] not in tags: raise ValueError("未知 float tag")
            return tags[data["value"]]
        if tag == "mapping": return _decode_mapping(value, "generic mapping")
        raise ValueError("generic object 必须是 Farcel tag")
    raise ValueError("不支持的 generic JSON value")


def _decode_mapping(document: object, name: str) -> dict[str, Any]:
    data = _object(document, name, _MAPPING_FIELDS)
    if data["$farcel_type"] != "mapping": raise ValueError(f"{name} 必须是 mapping tag")
    entries = _array(data["entries"], f"{name}.entries"); result: dict[str, Any] = {}
    for entry in entries:
        if not isinstance(entry, list) or len(entry) != 2: raise ValueError(f"{name} entry 必须是键值对")
        key = _string(entry[0], f"{name} key")
        if key in result: raise ValueError(f"{name} key 不能重复")
        result[key] = _decode_value(entry[1])
    return result


def _dumps(document: object) -> bytes: return json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
def _loads(data: bytes) -> object:
    if not isinstance(data, bytes): raise TypeError("data 必须是 bytes")
    return json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicate_object, parse_constant=_reject_constant)
def _no_duplicate_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError(f"重复 JSON key: {key}")
        result[key] = value
    return result
def _reject_constant(value: str): raise ValueError(f"不允许 JSON constant {value}")
def _object(value: object, name: str, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(fields): raise ValueError(f"{name} 字段结构无效")
    return value
def _array(value: object, name: str) -> list[Any]:
    if not isinstance(value, list): raise ValueError(f"{name} 必须是 array")
    return value
def _string(value: object, name: str) -> str:
    if not isinstance(value, str): raise ValueError(f"{name} 必须是字符串")
    return value
def _number(value: object, name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value): raise ValueError(f"{name} 必须是有限数值")
    return value
def _optional_number(value: object, name: str) -> int | float | None: return None if value is None else _number(value, name)
def _message_type(value: object) -> WorkerMessageType:
    try: return WorkerMessageType(_string(value, "message_type"))
    except ValueError as exc: raise ValueError("未知 message_type") from exc
def _require_valid(report):
    if not report.is_valid: raise ValueError("协议语义无效: " + ", ".join(issue.code for issue in report.issues))
def _error(phase: str, message: str, cause: Exception) -> EngineError: return EngineError(ErrorCode.VALIDATION_ERROR, message, {"phase": phase, "diagnostic": str(cause)})
