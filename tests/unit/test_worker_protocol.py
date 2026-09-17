from __future__ import annotations

from dataclasses import fields
import math
import unittest

from farcel.application.worker_protocol import WorkerProtocolValidator
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import SimulationConfig
from farcel.contracts.worker_protocol import (
    AdvanceToRequest,
    CreateRuntimeRequest,
    CreateRuntimeResponse,
    HasAssetRequest,
    HasAssetResponse,
    PutAssetRequest,
    PutAssetResponse,
    ReadOutputsResponse,
    RemoteError,
    RuntimeAck,
    RuntimeCommand,
    SetInputsRequest,
    WORKER_PROTOCOL_VERSION,
    WorkerMessageType,
    WorkerRequest,
    WorkerResponse,
    engine_error_from_remote_error,
    remote_error_from_engine_error,
)


_SHA256 = "a" * 64


class WorkerProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = WorkerProtocolValidator()

    def test_protocol_version_and_hello_ping_are_valid(self) -> None:
        self.assertEqual(WORKER_PROTOCOL_VERSION, "1.0")
        for message_type in (WorkerMessageType.HELLO, WorkerMessageType.PING):
            with self.subTest(message_type=message_type):
                self.assertTrue(self.validator.validate_request(self._request(message_type)).is_valid)
                self.assertTrue(self.validator.validate_response(self._response(message_type)).is_valid)

    def test_valid_asset_and_runtime_requests(self) -> None:
        requests = (
            self._request(WorkerMessageType.HAS_ASSET, HasAssetRequest(_SHA256)),
            self._request(WorkerMessageType.PUT_ASSET, PutAssetRequest(_SHA256, 0)),
            self._request(
                WorkerMessageType.CREATE_RUNTIME,
                CreateRuntimeRequest("node-a", _SHA256, SimulationConfig()),
            ),
            self._request(WorkerMessageType.INITIALIZE, RuntimeCommand("runtime-a")),
            self._request(WorkerMessageType.SET_INPUTS, SetInputsRequest("runtime-a", {"u": 1.0})),
            self._request(WorkerMessageType.ADVANCE_TO, AdvanceToRequest("runtime-a", 0.1)),
        )

        for request in requests:
            with self.subTest(message_type=request.message_type):
                self.assertTrue(self.validator.validate_request(request).is_valid)

    def test_asset_sha256_must_be_canonical_lowercase_hex(self) -> None:
        invalid_values = ("", "a" * 63, "a" * 65, "A" * 64, "g" * 64, 1)
        for value in invalid_values:
            with self.subTest(value=value):
                report = self.validator.validate_request(
                    self._request(WorkerMessageType.HAS_ASSET, HasAssetRequest(value))
                )
                self.assertIn("INVALID_ASSET_SHA256", _issue_codes(report))

    def test_put_asset_size_must_be_nonnegative_nonbool_integer(self) -> None:
        for size in (-1, True, 1.5, "1"):
            with self.subTest(size=size):
                report = self.validator.validate_request(
                    self._request(WorkerMessageType.PUT_ASSET, PutAssetRequest(_SHA256, size))
                )
                self.assertIn("INVALID_ASSET_SIZE", _issue_codes(report))

    def test_create_runtime_uses_asset_identity_without_model_path(self) -> None:
        request = CreateRuntimeRequest("node-a", _SHA256, SimulationConfig())

        self.assertEqual(
            tuple(field.name for field in fields(CreateRuntimeRequest)),
            ("node_id", "asset_sha256", "config"),
        )
        self.assertNotIn("model_path", tuple(field.name for field in fields(CreateRuntimeRequest)))
        self.assertTrue(
            self.validator.validate_request(self._request(WorkerMessageType.CREATE_RUNTIME, request)).is_valid
        )

    def test_invalid_target_time_and_input_values_are_reported(self) -> None:
        for target_time in (math.nan, math.inf, -math.inf, True, "0.1"):
            with self.subTest(target_time=target_time):
                report = self.validator.validate_request(
                    self._request(WorkerMessageType.ADVANCE_TO, AdvanceToRequest("runtime-a", target_time))
                )
                self.assertIn("INVALID_TARGET_TIME", _issue_codes(report))
        report = self.validator.validate_request(
            self._request(WorkerMessageType.SET_INPUTS, SetInputsRequest("runtime-a", ()))
        )
        self.assertIn("INVALID_INPUT_VALUES", _issue_codes(report))

    def test_payload_type_and_runtime_id_are_strictly_validated(self) -> None:
        report = self.validator.validate_request(
            self._request(WorkerMessageType.HAS_ASSET, PutAssetRequest(_SHA256, 1))
        )
        self.assertEqual(_issue_codes(report), ("INVALID_PROTOCOL_PAYLOAD",))
        report = self.validator.validate_request(
            self._request(WorkerMessageType.CLOSE, RuntimeCommand(" "))
        )
        self.assertEqual(_issue_codes(report), ("INVALID_RUNTIME_ID",))

    def test_response_payloads_and_success_error_state_are_validated(self) -> None:
        responses = (
            self._response(WorkerMessageType.HAS_ASSET, HasAssetResponse(_SHA256, True)),
            self._response(WorkerMessageType.PUT_ASSET, PutAssetResponse(_SHA256)),
            self._response(WorkerMessageType.CREATE_RUNTIME, CreateRuntimeResponse("runtime-a")),
            self._response(WorkerMessageType.READ_OUTPUTS, ReadOutputsResponse("runtime-a", {"y": 1.0})),
            self._response(WorkerMessageType.TERMINATE, RuntimeAck("runtime-a")),
        )
        for response in responses:
            with self.subTest(message_type=response.message_type):
                self.assertTrue(self.validator.validate_response(response).is_valid)

        report = self.validator.validate_response(
            WorkerResponse("request-a", "worker-a", WorkerMessageType.PING, True, error=RemoteError(ErrorCode.STEP_ERROR, "failed"))
        )
        self.assertIn("INVALID_PROTOCOL_RESPONSE_STATE", _issue_codes(report))

    def test_failed_response_requires_remote_error_and_no_payload(self) -> None:
        report = self.validator.validate_response(
            WorkerResponse("request-a", "worker-a", WorkerMessageType.PING, False, payload="bad")
        )
        self.assertEqual(
            _issue_codes(report),
            ("INVALID_PROTOCOL_RESPONSE_STATE", "INVALID_REMOTE_ERROR"),
        )
        valid = WorkerResponse(
            "request-a",
            "worker-a",
            WorkerMessageType.PING,
            False,
            error=RemoteError(ErrorCode.TIMEOUT, "timeout", {"phase": "ping"}),
        )
        self.assertTrue(self.validator.validate_response(valid).is_valid)

    def test_remote_error_conversion_preserves_stable_error_semantics(self) -> None:
        original = EngineError(ErrorCode.STEP_ERROR, "step failed", {"node_id": "node-a"})

        remote = remote_error_from_engine_error(original)
        restored = engine_error_from_remote_error(remote)

        self.assertEqual(remote, RemoteError(ErrorCode.STEP_ERROR, "step failed", {"node_id": "node-a"}))
        self.assertEqual((restored.code, restored.message, restored.details), (original.code, original.message, original.details))

    def test_response_correlation_rejects_every_mismatch(self) -> None:
        request = self._request(WorkerMessageType.PING)
        mismatches = (
            (WorkerResponse("other", "worker-a", WorkerMessageType.PING, True), "PROTOCOL_REQUEST_ID_MISMATCH"),
            (WorkerResponse("request-a", "other", WorkerMessageType.PING, True), "PROTOCOL_WORKER_ID_MISMATCH"),
            (WorkerResponse("request-a", "worker-a", WorkerMessageType.HELLO, True), "PROTOCOL_MESSAGE_TYPE_MISMATCH"),
            (WorkerResponse("request-a", "worker-a", WorkerMessageType.PING, True, protocol_version="2.0"), "PROTOCOL_VERSION_MISMATCH"),
        )
        for response, expected_code in mismatches:
            with self.subTest(expected_code=expected_code):
                self.assertIn(expected_code, _issue_codes(self.validator.validate_response(response, request=request)))

    def test_invalid_envelope_and_remote_error_are_reported(self) -> None:
        report = self.validator.validate_request(
            WorkerRequest(" ", " ", "ping", protocol_version="2.0")
        )
        self.assertEqual(
            _issue_codes(report),
            (
                "INVALID_PROTOCOL_VERSION",
                "INVALID_PROTOCOL_REQUEST_ID",
                "INVALID_PROTOCOL_WORKER_ID",
                "INVALID_PROTOCOL_MESSAGE_TYPE",
            ),
        )
        report = self.validator.validate_response(
            WorkerResponse("request-a", "worker-a", WorkerMessageType.PING, False, error=RemoteError("STEP_ERROR", " ", ()))
        )
        self.assertEqual(
            _issue_codes(report),
            ("INVALID_REMOTE_ERROR", "INVALID_REMOTE_ERROR", "INVALID_REMOTE_ERROR"),
        )

    @staticmethod
    def _request(message_type: WorkerMessageType, payload=None) -> WorkerRequest:
        return WorkerRequest("request-a", "worker-a", message_type, payload)

    @staticmethod
    def _response(message_type: WorkerMessageType, payload=None) -> WorkerResponse:
        return WorkerResponse("request-a", "worker-a", message_type, True, payload)


def _issue_codes(report) -> tuple[str, ...]:
    return tuple(issue.code for issue in report.issues)
