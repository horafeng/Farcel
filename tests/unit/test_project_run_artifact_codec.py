import json
import math
import unittest

from farcel.contracts import (
    Connection,
    EngineError,
    ErrorCode,
    GraphSimulationConfig,
    GraphSimulationResult,
    InputUpdate,
    InterfaceType,
    ModelNode,
    ModelNodeConfig,
    PortReference,
    ProjectAssetSnapshot,
    ProjectRunArtifact,
    SimulationCase,
    SimulationGraph,
    SimulationState,
)
from farcel.infrastructure.project import JsonProjectRunArtifactCodec


class ProjectRunArtifactCodecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.codec = JsonProjectRunArtifactCodec()

    def test_full_round_trip_keeps_case_nested_outputs_and_arrays(self) -> None:
        artifact = _artifact()

        restored = self.codec.loads(self.codec.dumps(artifact))

        self.assertEqual(restored.run_id, artifact.run_id)
        self.assertEqual(restored.project_id, artifact.project_id)
        self.assertEqual(restored.case_snapshot, artifact.case_snapshot)
        self.assertEqual(restored.asset_snapshots, artifact.asset_snapshots)
        self.assertEqual(restored.result.node_outputs["plant"]["array"][0], ((1.0, 2.0), (3.0, 4.0)))
        self.assertEqual(restored.result.node_outputs["controller"]["command"], (0, 1, 2))
        self.assertIs(restored.result.completion_state, SimulationState.COMPLETED)

    def test_non_finite_samples_use_tagged_standard_json_and_round_trip(self) -> None:
        text = self.codec.dumps(_artifact())

        self.assertIn('"$farcel_float": "nan"', text)
        self.assertIn('"$farcel_float": "positive_infinity"', text)
        self.assertIn('"$farcel_float": "negative_infinity"', text)
        self.assertNotIn(": NaN", text)
        self.assertNotIn(": Infinity", text)
        self.assertNotIn(": -Infinity", text)
        json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        values = self.codec.loads(text).result.node_outputs["plant"]["non_finite"]
        self.assertTrue(math.isnan(values[0]))
        self.assertTrue(math.isinf(values[1]) and values[1] > 0)
        self.assertTrue(math.isinf(values[2]) and values[2] < 0)

    def test_stopped_result_is_supported(self) -> None:
        artifact = _artifact(SimulationState.STOPPED)

        restored = self.codec.loads(self.codec.dumps(artifact))

        self.assertIs(restored.result.completion_state, SimulationState.STOPPED)

    def test_unknown_schema_malformed_or_top_level_shape_is_rejected(self) -> None:
        document = json.loads(self.codec.dumps(_artifact()))
        variants = []
        wrong_schema = dict(document); wrong_schema["schema_version"] = "9.0"; variants.append(json.dumps(wrong_schema))
        missing = dict(document); del missing["run_id"]; variants.append(json.dumps(missing))
        extra = dict(document); extra["extra"] = True; variants.append(json.dumps(extra))
        variants.append("{")
        for text in variants:
            with self.subTest(text=text[:20]):
                self._assert_format_error(lambda: self.codec.loads(text))

    def test_unknown_state_invalid_result_and_raw_nan_are_rejected(self) -> None:
        document = json.loads(self.codec.dumps(_artifact()))
        unknown_state = json.loads(json.dumps(document)); unknown_state["result"]["completion_state"] = "future"
        invalid_result = json.loads(json.dumps(document)); invalid_result["result"]["node_outputs"]["plant"]["integer"] = [1]
        raw_nan = json.dumps(document).replace("1.25", "NaN", 1)
        for text in (json.dumps(unknown_state), json.dumps(invalid_result), raw_nan):
            with self.subTest(text=text[:20]):
                self._assert_format_error(lambda: self.codec.loads(text))

    def test_bad_float_tag_and_mapping_sample_are_rejected(self) -> None:
        document = json.loads(self.codec.dumps(_artifact()))
        bad_tag = json.loads(json.dumps(document)); bad_tag["result"]["node_outputs"]["plant"]["non_finite"][0] = {"$farcel_float": "other"}
        mapping = json.loads(json.dumps(document)); mapping["result"]["node_outputs"]["plant"]["integer"][0] = {"unexpected": 1}
        for text in (json.dumps(bad_tag), json.dumps(mapping)):
            with self.subTest(text=text[:20]):
                self._assert_format_error(lambda: self.codec.loads(text))

    def test_unsupported_sample_objects_and_mappings_fail_encode(self) -> None:
        for value in (object(), {"not": "a sample"}):
            with self.subTest(value_type=type(value).__name__):
                artifact = _artifact_with_sample(value)
                self._assert_format_error(lambda: self.codec.dumps(artifact))

    def _assert_format_error(self, callback) -> None:
        with self.assertRaises(EngineError) as raised:
            callback()
        self.assertIs(raised.exception.code, ErrorCode.PROJECT_FORMAT_ERROR)


def _artifact(state: SimulationState = SimulationState.COMPLETED) -> ProjectRunArtifact:
    config = ModelNodeConfig(
        parameters={"gain": 1.25},
        initial_inputs={"u": 2.0},
        input_schedule=(InputUpdate(0.1, {"u": 3.0}),),
        selected_outputs=("array", "non_finite"),
        relative_tolerance=1e-5,
        execution_interface=InterfaceType.CO_SIMULATION,
    )
    case = SimulationCase(
        "case", "Nominal",
        SimulationGraph(
            nodes=(ModelNode("plant", "models/plant.fmu", config), ModelNode("controller", "models/controller.fmu")),
            connections=(Connection(PortReference("controller", "command"), PortReference("plant", "u")),),
        ),
        GraphSimulationConfig(stop_time=0.2, communication_step=0.1),
    )
    result = GraphSimulationResult(
        0.0, 0.2, 0.1, 2, 0.2, state, (0.0, 0.1, 0.2),
        {
            "plant": {
                "none": (None, None, None),
                "boolean": (True, False, True),
                "integer": (1, 2, 3),
                "float": (1.25, 2.5, 3.75),
                "string": ("a", "b", "c"),
                "array": (((1.0, 2.0), (3.0, 4.0)), ((5.0, 6.0), (7.0, 8.0)), ((9.0, 10.0), (11.0, 12.0))),
                "non_finite": (float("nan"), float("inf"), float("-inf")),
            },
            "controller": {"command": (0, 1, 2)},
        },
    )
    return ProjectRunArtifact(
        "run-1", "project-1", case,
        (ProjectAssetSnapshot("plant", "models/plant.fmu", "a" * 64), ProjectAssetSnapshot("controller", "models/controller.fmu", "b" * 64)),
        result,
    )


def _artifact_with_sample(value: object) -> ProjectRunArtifact:
    artifact = _artifact()
    result = GraphSimulationResult(
        artifact.result.start_time, artifact.result.stop_time, artifact.result.step_size,
        artifact.result.completed_steps, artifact.result.final_time, artifact.result.completion_state,
        artifact.result.timestamps, {"plant": {"bad": (value, value, value)}},
    )
    return ProjectRunArtifact(
        artifact.run_id, artifact.project_id, artifact.case_snapshot,
        artifact.asset_snapshots, result,
    )
