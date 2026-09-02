import unittest
from dataclasses import replace
from unittest.mock import Mock

from farcel.application.engine import FarcelEngine
from farcel.application.validation import validate_config
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import (
    CapabilitySet,
    InterfaceType,
    InputUpdate,
    ModelMetadata,
    SimulationConfig,
    VariableMetadata,
)


def metadata(
    *,
    interface: InterfaceType = InterfaceType.CO_SIMULATION,
    executable: bool = True,
) -> ModelMetadata:
    return ModelMetadata(
        model_id="example",
        source_path="example.fmu",
        fmi_version="2.0",
        model_name="Example",
        interface_types=(interface,),
        executable_interface=(
            InterfaceType.CO_SIMULATION
            if interface is InterfaceType.CO_SIMULATION and executable
            else None
        ),
        capabilities=CapabilitySet(can_execute=executable),
        variables=(
            VariableMetadata(
                "speed", 1, "Float64", causality="output", variability="continuous"
            ),
            VariableMetadata(
                "gain", 2, "Float64", causality="parameter", variability="fixed",
                minimum=0.0, maximum=10.0,
            ),
        ),
    )


def structural_metadata() -> ModelMetadata:
    return replace(
        metadata(),
        fmi_version="3.0",
        variables=(
            VariableMetadata("m", 1, "UInt64", causality="structuralParameter", start=3, minimum=0, maximum=5),
            VariableMetadata("n", 2, "UInt64", causality="structuralParameter", start=3, minimum=0, maximum=5),
            VariableMetadata("r", 3, "UInt64", causality="structuralParameter", start=3, minimum=0, maximum=5),
            VariableMetadata("A", 4, "Float64", causality="parameter", shape=(3, 3), dimension_value_references=(2, 2)),
            VariableMetadata("B", 5, "Float64", causality="parameter", shape=(3, 3), dimension_value_references=(2, 1)),
            VariableMetadata("u", 6, "Float64", causality="input", shape=(3,), dimension_value_references=(1,)),
            VariableMetadata("y", 7, "Float64", causality="output", shape=(3,), dimension_value_references=(3,)),
            VariableMetadata("structural_array", 8, "UInt64", causality="structuralParameter", shape=(1,)),
        ),
    )


class ValidationTests(unittest.TestCase):
    def test_legacy_positional_variable_metadata_keeps_shape_position(self) -> None:
        variable = VariableMetadata(
            "matrix", 1, "Float64", None, None, None, None, None, None,
            None, None, None, None, (2, 3),
        )

        self.assertEqual(variable.shape, (2, 3))
        self.assertEqual(variable.dimension_value_references, ())

    def test_legacy_positional_config_keeps_existing_field_positions(self) -> None:
        config = SimulationConfig(
            "1.0",
            0.0,
            2.0,
            0.1,
            0.2,
            None,
            {"gain": 2.0},
            {"command": 1.0},
            ("speed",),
            (),
        )

        self.assertEqual(config.selected_outputs, ("speed",))
        self.assertEqual(config.input_schedule, ())
        self.assertIsNone(config.execution_interface)

    def test_valid_co_simulation_config(self) -> None:
        report = validate_config(
            metadata(),
            SimulationConfig(parameters={"gain": 2.0}, selected_outputs=("speed",)),
        )
        self.assertTrue(report.is_valid)

    def test_execution_interface_uses_safe_phase_3_0b_semantics(self) -> None:
        dual_interface_metadata = replace(
            metadata(),
            interface_types=(
                InterfaceType.CO_SIMULATION,
                InterfaceType.MODEL_EXCHANGE,
            ),
        )

        self.assertTrue(validate_config(dual_interface_metadata, SimulationConfig()).is_valid)
        self.assertTrue(
            validate_config(
                dual_interface_metadata,
                SimulationConfig(execution_interface=InterfaceType.CO_SIMULATION),
            ).is_valid
        )

        model_exchange = validate_config(
            dual_interface_metadata,
            SimulationConfig(execution_interface=InterfaceType.MODEL_EXCHANGE),
        )
        self.assertEqual(
            [(issue.field, issue.code) for issue in model_exchange.issues],
            [("execution_interface", ErrorCode.UNSUPPORTED_INTERFACE.value)],
        )
        self.assertIn("尚未在当前里程碑启用", model_exchange.issues[0].message)

        scheduled_execution = validate_config(
            dual_interface_metadata,
            SimulationConfig(execution_interface=InterfaceType.SCHEDULED_EXECUTION),
        )
        self.assertEqual(
            [(issue.field, issue.code) for issue in scheduled_execution.issues],
            [("execution_interface", ErrorCode.UNSUPPORTED_INTERFACE.value)],
        )

    def test_explicit_co_simulation_requires_co_simulation_capability(self) -> None:
        missing_interface = validate_config(
            metadata(interface=InterfaceType.MODEL_EXCHANGE, executable=False),
            SimulationConfig(execution_interface=InterfaceType.CO_SIMULATION),
        )

        self.assertEqual(
            [(issue.field, issue.code) for issue in missing_interface.issues],
            [("model", ErrorCode.UNSUPPORTED_INTERFACE.value)],
        )
        missing_binary = validate_config(
            metadata(executable=False),
            SimulationConfig(execution_interface=InterfaceType.CO_SIMULATION),
        )
        self.assertEqual(
            [(issue.field, issue.code) for issue in missing_binary.issues],
            [("model", ErrorCode.PLATFORM_BINARY_MISSING.value)],
        )

    def test_omitted_output_interval_defaults_to_communication_step(self) -> None:
        config = SimulationConfig(communication_step=0.1)

        self.assertIsNone(config.output_interval)
        self.assertTrue(validate_config(metadata(), config).is_valid)

    def test_rejects_invalid_or_unaligned_output_interval(self) -> None:
        for output_interval in (0.0, -0.1, float("nan"), float("inf")):
            with self.subTest(output_interval=output_interval):
                report = validate_config(
                    metadata(), SimulationConfig(output_interval=output_interval)
                )
                self.assertIn(
                    "INVALID_OUTPUT_INTERVAL", {issue.code for issue in report.issues}
                )

        report = validate_config(
            metadata(),
            SimulationConfig(communication_step=0.03, output_interval=0.1),
        )
        issue = next(
            issue
            for issue in report.issues
            if issue.code == "OUTPUT_INTERVAL_NOT_COMMUNICATION_ALIGNED"
        )
        self.assertEqual(issue.field, "output_interval")

    def test_rejects_start_time_equal_to_or_after_stop_time(self) -> None:
        for start_time, stop_time in ((1.0, 1.0), (2.0, 1.0)):
            with self.subTest(start_time=start_time, stop_time=stop_time):
                report = validate_config(
                    metadata(),
                    SimulationConfig(start_time=start_time, stop_time=stop_time),
                )
                self.assertIn("INVALID_TIME_RANGE", {i.code for i in report.issues})

    def test_rejects_zero_or_negative_step_size(self) -> None:
        for step_size in (0.0, -0.1):
            with self.subTest(step_size=step_size):
                report = validate_config(
                    metadata(), SimulationConfig(communication_step=step_size)
                )
                self.assertIn("INVALID_STEP_SIZE", {i.code for i in report.issues})

    def test_rejects_unknown_parameter(self) -> None:
        report = validate_config(
            metadata(), SimulationConfig(parameters={"missing": 1.0})
        )
        self.assertIn("UNKNOWN_PARAMETER", {i.code for i in report.issues})

    def test_rejects_unknown_output(self) -> None:
        report = validate_config(
            metadata(), SimulationConfig(selected_outputs=("missing",))
        )
        self.assertIn("UNKNOWN_OUTPUT", {i.code for i in report.issues})

    def test_rejects_fmi3_binary_and_clock_selected_outputs_before_runtime(self) -> None:
        model = replace(
            metadata(),
            fmi_version="3.0",
            variables=metadata().variables + (
                VariableMetadata("binary_out", 3, "Binary", causality="output"),
                VariableMetadata("clock_out", 4, "Clock", causality="output"),
                VariableMetadata("string_out", 5, "String", causality="output"),
            ),
        )

        supported = validate_config(
            model, SimulationConfig(selected_outputs=("speed", "string_out"))
        )
        self.assertTrue(supported.is_valid)
        rejected = validate_config(
            model, SimulationConfig(selected_outputs=("binary_out", "clock_out"))
        )
        self.assertEqual(
            {issue.code for issue in rejected.issues}, {"UNSUPPORTED_OUTPUT_TYPE"}
        )

    def test_validates_initial_inputs_names_causality_types_and_ranges(self) -> None:
        model = replace(
            metadata(),
            variables=metadata().variables + (
                VariableMetadata("real_in", 3, "Real", causality="input", minimum=0.0, maximum=2.0),
                VariableMetadata("int_in", 4, "Integer", causality="input"),
                VariableMetadata("bool_in", 5, "Boolean", causality="input"),
                VariableMetadata("string_in", 6, "String", causality="input"),
            ),
        )
        valid = validate_config(
            model,
            SimulationConfig(initial_inputs={
                "real_in": 1.0, "int_in": 2, "bool_in": True, "string_in": "ok",
            }),
        )
        self.assertTrue(valid.is_valid)

        cases = {
            "missing": "UNKNOWN_INPUT",
            "speed": "INVALID_INPUT_CAUSALITY",
            "int_in": "INVALID_INPUT_TYPE",
        }
        values = {"missing": 1.0, "speed": 1.0, "int_in": True}
        for name, code in cases.items():
            with self.subTest(name=name):
                report = validate_config(model, SimulationConfig(initial_inputs={name: values[name]}))
                self.assertIn(code, {issue.code for issue in report.issues})

        for value, code in ((-0.1, "INPUT_BELOW_MINIMUM"), (2.1, "INPUT_ABOVE_MAXIMUM")):
            with self.subTest(value=value):
                report = validate_config(model, SimulationConfig(initial_inputs={"real_in": value}))
                self.assertIn(code, {issue.code for issue in report.issues})

    def test_validates_resolved_fmi3_array_values(self) -> None:
        model = replace(
            metadata(),
            fmi_version="3.0",
            variables=metadata().variables + (
                VariableMetadata(
                    "matrix", 3, "Float64", causality="parameter", shape=(2, 2),
                    minimum=0.0, maximum=2.0,
                ),
                VariableMetadata("vector", 4, "Float64", causality="input", shape=(2,)),
            ),
        )
        valid = validate_config(
            model,
            SimulationConfig(
                stop_time=0.03,
                communication_step=0.01,
                parameters={"matrix": ((1.0, 0.0), (0.0, 1.0))},
                initial_inputs={"vector": [1.0, 2.0]},
                input_schedule=(InputUpdate(0.01, {"vector": (2.0, 1.0)}),),
            ),
        )
        self.assertTrue(valid.is_valid)

        cases = (
            (SimulationConfig(parameters={"matrix": (1.0, 0.0, 0.0, 1.0)}), "INVALID_ARRAY_SHAPE"),
            (SimulationConfig(parameters={"matrix": ((1.0, "bad"), (0.0, 1.0))}), "INVALID_ARRAY_ELEMENT_TYPE"),
            (SimulationConfig(parameters={"matrix": ((-1.0, 0.0), (0.0, 1.0))}), "ARRAY_ELEMENT_BELOW_MINIMUM"),
            (SimulationConfig(initial_inputs={"vector": [[1.0, 2.0]]}), "INVALID_ARRAY_SHAPE"),
            (SimulationConfig(initial_inputs={"vector": (1.0, True)}), "INVALID_ARRAY_ELEMENT_TYPE"),
        )
        for config, code in cases:
            with self.subTest(code=code):
                report = validate_config(model, config)
                self.assertIn(code, {issue.code for issue in report.issues})

    def test_validates_structural_overrides_before_dynamic_array_shapes(self) -> None:
        model = structural_metadata()
        matrix = lambda rows, columns: tuple(
            tuple(0.0 for _ in range(columns)) for _ in range(rows)
        )
        valid = SimulationConfig(
            stop_time=0.03,
            communication_step=0.01,
            parameters={"m": 2, "n": 4, "r": 1, "A": matrix(4, 4), "B": matrix(4, 2)},
            initial_inputs={"u": (1.0, 2.0)},
            input_schedule=(InputUpdate(0.01, {"u": (2.0, 1.0)}),),
        )
        self.assertTrue(validate_config(model, valid).is_valid)

        cases = (
            (SimulationConfig(parameters={"missing": 2}), "UNKNOWN_PARAMETER"),
            (SimulationConfig(parameters={"n": True}), "INVALID_PARAMETER_TYPE"),
            (SimulationConfig(parameters={"n": -1}), "PARAMETER_OUT_OF_TYPE_RANGE"),
            (SimulationConfig(parameters={"n": 6}), "PARAMETER_ABOVE_MAXIMUM"),
            (SimulationConfig(parameters={"structural_array": (1,)}), "UNSUPPORTED_STRUCTURAL_PARAMETER_TYPE"),
            (SimulationConfig(parameters={"m": 2, "n": 4, "A": matrix(3, 3)}), "INVALID_ARRAY_SHAPE"),
            (SimulationConfig(parameters={"m": 2, "n": 4, "B": matrix(3, 3)}), "INVALID_ARRAY_SHAPE"),
            (SimulationConfig(parameters={"m": 2}, initial_inputs={"u": (1.0, 2.0, 3.0)}), "INVALID_ARRAY_SHAPE"),
        )
        for config, code in cases:
            with self.subTest(code=code):
                self.assertIn(code, {issue.code for issue in validate_config(model, config).issues})

    def test_validates_fmi3_integer_scalar_ranges(self) -> None:
        model = replace(
            metadata(),
            fmi_version="3.0",
            variables=metadata().variables + tuple(
                VariableMetadata(name, index + 10, data_type, causality="input")
                for index, (name, data_type) in enumerate((
                    ("i8", "Int8"), ("u8", "UInt8"), ("i16", "Int16"),
                    ("u16", "UInt16"), ("i32", "Int32"), ("u32", "UInt32"),
                    ("i64", "Int64"), ("u64", "UInt64"),
                ))
            ),
        )
        self.assertTrue(validate_config(model, SimulationConfig(initial_inputs={
            "i8": -128, "u8": 255, "i16": -32768, "u16": 65535,
            "i32": -(2**31), "u32": 2**32 - 1,
            "i64": -(2**63), "u64": 2**64 - 1,
        })).is_valid)
        for name, value in (("i8", 128), ("u8", -1), ("u64", 2**64)):
            with self.subTest(name=name):
                report = validate_config(model, SimulationConfig(initial_inputs={name: value}))
                self.assertIn("INPUT_OUT_OF_TYPE_RANGE", {issue.code for issue in report.issues})

    def test_input_schedule_requires_ordered_communication_points_and_valid_values(self) -> None:
        model = replace(
            metadata(),
            variables=metadata().variables + (
                VariableMetadata("command", 3, "Float64", causality="input"),
            ),
        )
        valid = validate_config(
            model,
            SimulationConfig(
                stop_time=0.03,
                communication_step=0.01,
                input_schedule=(
                    InputUpdate(0.0, {"command": 1.0}),
                    InputUpdate(0.01, {"command": 2.0}),
                ),
            ),
        )
        self.assertTrue(valid.is_valid)
        invalid = validate_config(
            model,
            SimulationConfig(
                stop_time=0.03,
                communication_step=0.01,
                input_schedule=(
                    InputUpdate(0.015, {"command": 1.0}),
                    InputUpdate(0.01, {"missing": 2.0}),
                ),
            ),
        )
        self.assertEqual(
            {issue.code for issue in invalid.issues},
            {"INPUT_TIME_NOT_COMMUNICATION_POINT", "INPUT_TIMES_NOT_INCREASING", "UNKNOWN_INPUT"},
        )

    def test_rejects_parameter_with_wrong_type_or_causality(self) -> None:
        report = validate_config(
            metadata(),
            SimulationConfig(parameters={"gain": "fast", "speed": 1.0}),
        )
        self.assertEqual(
            {i.code for i in report.issues},
            {"INVALID_PARAMETER_TYPE", "INVALID_PARAMETER_CAUSALITY"},
        )

    def test_rejects_fmu_outside_current_execution_policy(self) -> None:
        engine = FarcelEngine(importer=Mock())
        with self.assertRaises(EngineError) as raised:
            engine.validate_config(
                metadata(interface=InterfaceType.MODEL_EXCHANGE, executable=False),
                SimulationConfig(),
            )
        self.assertEqual(raised.exception.code, ErrorCode.CONFIG_ERROR)
        self.assertEqual(
            raised.exception.details["issues"][0]["code"],
            ErrorCode.UNSUPPORTED_INTERFACE.value,
        )

    def test_application_facade_returns_stable_config_error(self) -> None:
        engine = FarcelEngine(importer=Mock())
        with self.assertRaises(EngineError) as raised:
            engine.validate_config(
                metadata(), SimulationConfig(start_time=1.0, stop_time=1.0)
            )
        self.assertEqual(raised.exception.code, ErrorCode.CONFIG_ERROR)
        self.assertEqual(
            raised.exception.details["issues"][0]["code"], "INVALID_TIME_RANGE"
        )

    def test_reports_multiple_errors(self) -> None:
        config = SimulationConfig(
            start_time=1.0, stop_time=1.0, communication_step=0.0,
            output_interval=-1.0, selected_outputs=("missing",),
        )
        report = validate_config(
            metadata(interface=InterfaceType.MODEL_EXCHANGE, executable=False), config
        )
        self.assertEqual(
            {i.field for i in report.issues},
            {"stop_time", "communication_step", "output_interval", "model", "selected_outputs"},
        )
