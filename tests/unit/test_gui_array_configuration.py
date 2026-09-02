"""Array configuration behavior using only Farcel public contracts."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

from farcel.contracts import (
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    SimulationResult,
    SimulationState,
    VariableMetadata,
)
from gui.configuration_file import configuration_payload, read_configuration_payload
from gui.presenter import result_table_data


class ArrayConfigurationFileTest(unittest.TestCase):
    def test_json_round_trip_restores_nested_tuples(self) -> None:
        payload = configuration_payload(
            Path("array-model.fmu"),
            SimulationConfig(
                parameters={"matrix": ((1.0, 0.0), (0.0, 1.0))},
                initial_inputs={"vector": (2.0, 3.0)},
            ),
        )

        _, restored = read_configuration_payload(json.loads(json.dumps(payload)))

        self.assertEqual(restored.parameters["matrix"], ((1.0, 0.0), (0.0, 1.0)))
        self.assertEqual(restored.initial_inputs["vector"], (2.0, 3.0))


class ArrayResultPresentationTest(unittest.TestCase):
    def test_array_output_is_retained_in_result_table_cells(self) -> None:
        result = SimulationResult(
            fmu_path="array-model.fmu",
            start_time=0.0,
            stop_time=0.1,
            step_size=0.1,
            completed_steps=1,
            final_time=0.1,
            completion_state=SimulationState.COMPLETED,
            timestamps=(0.0, 0.1),
            outputs={"vector": ((1.0, 2.0), (3.0, 4.0))},
        )

        headers, rows = result_table_data(result)

        self.assertEqual(headers, ("时间", "vector"))
        self.assertEqual(rows[0], ("0.0", "(1.0, 2.0)"))
        self.assertEqual(rows[1], ("0.1", "(3.0, 4.0)"))


HAS_PYSIDE6 = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(HAS_PYSIDE6, "PySide6 is not installed")
class ArrayConfigurationGuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def test_array_editors_build_nested_tuple_configuration(self) -> None:
        from PySide6.QtWidgets import QPlainTextEdit

        from gui.main import MainWindow

        window = MainWindow()
        metadata = ModelMetadata(
            model_id="array-model",
            source_path="array-model.fmu",
            fmi_version="3.0",
            model_name="Array model",
            interface_types=(InterfaceType.CO_SIMULATION,),
            executable_interface=InterfaceType.CO_SIMULATION,
            variables=(
                VariableMetadata("m", 1, "UInt64", causality="structuralParameter"),
                VariableMetadata(
                    "matrix",
                    2,
                    "Float64",
                    causality="parameter",
                    shape=(3, 3),
                    dimension_value_references=(1, 1),
                ),
                VariableMetadata(
                    "vector",
                    3,
                    "Float64",
                    causality="input",
                    shape=(3,),
                    dimension_value_references=(1,),
                ),
                VariableMetadata("result", 4, "Float64", causality="output", shape=(3,)),
            ),
        )
        try:
            window.current_metadata = metadata
            window._load_configuration_defaults(metadata)
            matrix_editor = window.parameter_widgets["matrix"][1]
            vector_editor = window.initial_input_widgets["vector"][1]
            self.assertIsInstance(matrix_editor, QPlainTextEdit)
            self.assertIsInstance(vector_editor, QPlainTextEdit)

            window.parameter_widgets["m"][1].setText("2")
            matrix_editor.setPlainText("[[1, 0], [0, 1]]")
            vector_editor.setPlainText("[3, 4]")
            config = window._build_simulation_config()

            self.assertEqual(config.parameters["matrix"], ((1, 0), (0, 1)))
            self.assertEqual(config.initial_inputs["vector"], (3, 4))
            self.assertEqual(config.selected_outputs, ("result",))
        finally:
            window.close()
            self.application.processEvents()
