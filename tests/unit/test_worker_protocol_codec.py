from __future__ import annotations

import json
import math
import unittest

from farcel.application.worker_protocol import WorkerProtocolValidator
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import InputUpdate, InterfaceType, SimulationConfig
from farcel.contracts.worker_protocol import (
    AdvanceToRequest, CreateRuntimeRequest, CreateRuntimeResponse, HasAssetRequest,
    HasAssetResponse, PutAssetRequest, PutAssetResponse, ReadOutputsResponse,
    RemoteError, RuntimeAck, RuntimeCommand, SetInputsRequest, WorkerMessageType,
    WorkerRequest, WorkerResponse,
)
from farcel.infrastructure.worker_protocol.json_codec import JsonWorkerProtocolCodec


_SHA = "a" * 64


class WorkerProtocolCodecTests(unittest.TestCase):
    def setUp(self): self.codec = JsonWorkerProtocolCodec(WorkerProtocolValidator())
    def test_all_request_messages_round_trip(self):
        payloads = {WorkerMessageType.HELLO: None, WorkerMessageType.PING: None, WorkerMessageType.HAS_ASSET: HasAssetRequest(_SHA), WorkerMessageType.PUT_ASSET: PutAssetRequest(_SHA, 3), WorkerMessageType.CREATE_RUNTIME: CreateRuntimeRequest("n", _SHA, self._config()), WorkerMessageType.INITIALIZE: RuntimeCommand("r"), WorkerMessageType.SET_INPUTS: SetInputsRequest("r", {"u": (1, 2)}), WorkerMessageType.ADVANCE_TO: AdvanceToRequest("r", .1), WorkerMessageType.READ_OUTPUTS: RuntimeCommand("r"), WorkerMessageType.TERMINATE: RuntimeCommand("r"), WorkerMessageType.CLOSE: RuntimeCommand("r")}
        for message_type, payload in payloads.items():
            with self.subTest(message_type=message_type):
                request = WorkerRequest("id", "worker", message_type, payload)
                self.assertEqual(self.codec.decode_request(self.codec.encode_request(request)), request)
    def test_all_success_responses_and_error_round_trip(self):
        payloads = {WorkerMessageType.HELLO: None, WorkerMessageType.PING: None, WorkerMessageType.HAS_ASSET: HasAssetResponse(_SHA, True), WorkerMessageType.PUT_ASSET: PutAssetResponse(_SHA), WorkerMessageType.CREATE_RUNTIME: CreateRuntimeResponse("r"), WorkerMessageType.INITIALIZE: RuntimeAck("r"), WorkerMessageType.SET_INPUTS: RuntimeAck("r"), WorkerMessageType.ADVANCE_TO: RuntimeAck("r"), WorkerMessageType.READ_OUTPUTS: ReadOutputsResponse("r", {"y": "中文"}), WorkerMessageType.TERMINATE: RuntimeAck("r"), WorkerMessageType.CLOSE: RuntimeAck("r")}
        for message_type, payload in payloads.items():
            with self.subTest(message_type=message_type):
                response = WorkerResponse("id", "worker", message_type, True, payload)
                self.assertEqual(self.codec.decode_response(self.codec.encode_response(response)), response)
        failed = WorkerResponse("id", "worker", WorkerMessageType.ADVANCE_TO, False, error=RemoteError(ErrorCode.STEP_ERROR, "推进失败", {"x": 1}))
        self.assertEqual(self.codec.decode_response(self.codec.encode_response(failed)), failed)
    def test_deterministic_config_and_generic_value_round_trip(self):
        request = WorkerRequest("id", "worker", WorkerMessageType.CREATE_RUNTIME, CreateRuntimeRequest("n", _SHA, self._config()))
        encoded = self.codec.encode_request(request)
        self.assertEqual(encoded, self.codec.encode_request(request))
        self.assertEqual(self.codec.decode_request(encoded), request)
        outputs = {"none": None, "bool": True, "int": 1, "float": 1.5, "tuple": ("x", [2]), "mapping": {"$farcel_type": "float", "value": "nan"}}
        response = WorkerResponse("id", "worker", WorkerMessageType.READ_OUTPUTS, True, ReadOutputsResponse("r", outputs))
        decoded = self.codec.decode_response(self.codec.encode_response(response)).payload.outputs
        self.assertEqual(decoded["tuple"], ("x", (2,)))
        self.assertEqual(decoded["mapping"], outputs["mapping"])
    def test_nonfinite_float_round_trip_uses_explicit_tag(self):
        response = WorkerResponse("id", "worker", WorkerMessageType.READ_OUTPUTS, True, ReadOutputsResponse("r", {"nan": math.nan, "plus": math.inf, "minus": -math.inf}))
        encoded = self.codec.encode_response(response)
        self.assertNotIn(b"NaN", encoded); self.assertIn(b"$farcel_type", encoded)
        values = self.codec.decode_response(encoded).payload.outputs
        self.assertTrue(math.isnan(values["nan"])); self.assertEqual(values["plus"], math.inf); self.assertEqual(values["minus"], -math.inf)
    def test_invalid_wire_and_invalid_dto_are_stable_engine_errors(self):
        invalid = [b"\xff", b"{", b"[]", b"NaN", b'{"protocol_version":"1.0","protocol_version":"1.0"}', b'{"protocol_version":"1.0"}']
        for data in invalid:
            with self.subTest(data=data): self._assert_decode_error(data)
        self._assert_decode_error(json.dumps({"protocol_version":"1.0","request_id":"id","worker_id":"w","message_type":"unknown","payload":None}).encode())
        self._assert_decode_error(b'{"protocol_version":"1.0","request_id":"id","worker_id":"w","message_type":"ping","payload":{},"extra":1}')
        self._assert_decode_error(b'{"protocol_version":"1.0","request_id":"id","worker_id":"w","message_type":"ping","payload":{}}')
        self._assert_decode_error(b'{"protocol_version":"1.0","request_id":"id","worker_id":"w","message_type":"has_asset","payload":{"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","extra":1}}')
        bad = WorkerRequest(" ", "w", WorkerMessageType.PING)
        with self.assertRaises(EngineError) as raised: self.codec.encode_request(bad)
        self.assertEqual((raised.exception.code, raised.exception.details["phase"]), (ErrorCode.VALIDATION_ERROR, "worker_protocol_encode"))
    def test_unknown_error_and_unsupported_values_are_rejected(self):
        response = b'{"protocol_version":"1.0","request_id":"id","worker_id":"w","message_type":"ping","ok":false,"payload":null,"error":{"code":"UNKNOWN","message":"x","details":{"$farcel_type":"mapping","entries":[]}}}'
        self._assert_response_decode_error(response)
        bad = WorkerRequest("id", "w", WorkerMessageType.SET_INPUTS, SetInputsRequest("r", {"bad": {1}}))
        with self.assertRaises(EngineError): self.codec.encode_request(bad)
        bad = WorkerRequest("id", "w", WorkerMessageType.SET_INPUTS, SetInputsRequest("r", {1: "x"}))
        with self.assertRaises(EngineError): self.codec.encode_request(bad)
    def _assert_decode_error(self, data):
        with self.assertRaises(EngineError) as raised: self.codec.decode_request(data)
        self.assertEqual((raised.exception.code, raised.exception.details["phase"]), (ErrorCode.VALIDATION_ERROR, "worker_protocol_decode"))
    def _assert_response_decode_error(self, data):
        with self.assertRaises(EngineError) as raised: self.codec.decode_response(data)
        self.assertEqual(raised.exception.code, ErrorCode.VALIDATION_ERROR)
    @staticmethod
    def _config(): return SimulationConfig(schema_version="1.0", start_time=1.0, stop_time=2.0, communication_step=.1, output_interval=.2, relative_tolerance=1e-4, parameters={"gain": (1, 2)}, initial_inputs={"u": "值"}, selected_outputs=("y",), input_schedule=(InputUpdate(1.0, {"u": 0}),), execution_interface=InterfaceType.CO_SIMULATION)
