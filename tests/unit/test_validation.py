import unittest

from farcel.application.validation import validate_config
from farcel.contracts.models import (
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    VariableMetadata,
)


def metadata(*, interface: InterfaceType = InterfaceType.CO_SIMULATION) -> ModelMetadata:
    return ModelMetadata(
        model_id="example",
        source_path="example.fmu",
        fmi_version="2.0",
        model_name="Example",
        interface_types=(interface,),
        variables=(VariableMetadata("speed", 1, "float64"),),
    )


class ValidationTests(unittest.TestCase):
    def test_valid_co_simulation_config(self) -> None:
        report = validate_config(
            metadata(), SimulationConfig(selected_outputs=("speed",))
        )
        self.assertTrue(report.is_valid)

    def test_reports_time_step_interface_and_output_errors(self) -> None:
        config = SimulationConfig(
            start_time=1.0,
            stop_time=1.0,
            communication_step=0.0,
            output_interval=-1.0,
            selected_outputs=("missing",),
        )
        report = validate_config(
            metadata(interface=InterfaceType.MODEL_EXCHANGE), config
        )
        self.assertEqual(
            {issue.field for issue in report.issues},
            {
                "stop_time",
                "communication_step",
                "output_interval",
                "interface_type",
                "selected_outputs",
            },
        )
