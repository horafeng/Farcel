from __future__ import annotations

from dataclasses import fields
from pathlib import Path
import tempfile
import unittest

from farcel.application.validation import validate_config
from farcel.contracts import (
    CapabilitySet,
    InterfaceCapability,
    InterfaceType,
    ModelMetadata,
    ModelicaCompileRequest,
    SimulationConfig,
    VariableMetadata,
)
from farcel.contracts.modelica_validation import validate_modelica_compile_request


class ModelicaParameterBoundaryTests(unittest.TestCase):
    def test_compile_request_keeps_the_public_compile_only_shape(self) -> None:
        self.assertEqual(
            (
                "source_path",
                "model_name",
                "output_directory",
                "compiler_path",
                "fmi_version",
                "fmu_type",
                "timeout_seconds",
            ),
            tuple(field.name for field in fields(ModelicaCompileRequest)),
        )

    def test_compile_validation_rejects_unsupported_shapes_before_compiler_use(self) -> None:
        with self._request_context() as (source, output):
            cases = (
                (
                    ModelicaCompileRequest(source, "Example.Model", output, fmi_version="3.0"),
                    "fmi_version",
                    "UNSUPPORTED_FMI_VERSION",
                ),
                (
                    ModelicaCompileRequest(source, "Example.Model", output, fmu_type="me"),
                    "fmu_type",
                    "UNSUPPORTED_FMU_TYPE",
                ),
                (
                    ModelicaCompileRequest(source, "Example.Model", output, timeout_seconds=0),
                    "timeout_seconds",
                    "TIMEOUT_SECONDS_OUT_OF_RANGE",
                ),
                (
                    ModelicaCompileRequest(source, "Bad); unsafe", output),
                    "model_name",
                    "INVALID_MODEL_NAME",
                ),
            )
            for request, field, code in cases:
                with self.subTest(field=field, code=code):
                    issues = validate_modelica_compile_request(request).issues
                    self.assertIn((field, code), {(issue.field, issue.code) for issue in issues})

    def test_parameters_and_inputs_are_distinct_validated_configuration_fields(self) -> None:
        metadata = ModelMetadata(
            model_id="model",
            source_path="model.fmu",
            fmi_version="2.0",
            model_name="Boundary",
            interface_types=(InterfaceType.CO_SIMULATION,),
            interface_capabilities=(
                InterfaceCapability(InterfaceType.CO_SIMULATION, "Boundary", can_execute=True),
            ),
            executable_interface=InterfaceType.CO_SIMULATION,
            capabilities=CapabilitySet(can_execute=True),
            variables=(
                VariableMetadata("T", 1, "Real", causality="parameter", minimum=1e-9),
                VariableMetadata("u", 2, "Real", causality="input"),
                VariableMetadata("y", 3, "Real", causality="output"),
            ),
        )

        valid = SimulationConfig(parameters={"T": 0.5}, initial_inputs={"u": 1.0})
        self.assertTrue(validate_config(metadata, valid).is_valid)

        parameter_as_input = validate_config(metadata, SimulationConfig(initial_inputs={"T": 0.5}))
        input_as_parameter = validate_config(metadata, SimulationConfig(parameters={"u": 1.0}))
        self.assertIn("INVALID_INPUT_CAUSALITY", {issue.code for issue in parameter_as_input.issues})
        self.assertIn("INVALID_PARAMETER_CAUSALITY", {issue.code for issue in input_as_parameter.issues})

    @staticmethod
    def _request_context():
        class _Context:
            def __enter__(self) -> tuple[Path, Path]:
                self._temporary_directory = tempfile.TemporaryDirectory()
                root = Path(self._temporary_directory.name)
                source = root / "Source.mo"
                source.write_text("model Source end Source;", encoding="utf-8")
                output = root / "output"
                output.mkdir()
                return source, output

            def __exit__(self, *args: object) -> None:
                self._temporary_directory.cleanup()

        return _Context()


if __name__ == "__main__":
    unittest.main()
