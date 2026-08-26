import unittest
from unittest.mock import Mock

from farcel.application.engine import FarcelEngine
from farcel.application.validation import validate_config
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import (
    CapabilitySet,
    InterfaceType,
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


class ValidationTests(unittest.TestCase):
    def test_valid_co_simulation_config(self) -> None:
        report = validate_config(
            metadata(),
            SimulationConfig(parameters={"gain": 2.0}, selected_outputs=("speed",)),
        )
        self.assertTrue(report.is_valid)

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
