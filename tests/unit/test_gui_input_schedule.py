"""Tests for the GUI-owned JSON input schedule representation."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

from farcel.contracts import (
    InputUpdate,
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    VariableMetadata,
)
from gui.configuration_file import configuration_payload, read_configuration_payload


class InputScheduleConfigurationFileTest(unittest.TestCase):
    def test_schedule_round_trips_through_json_configuration(self) -> None:
        payload = configuration_payload(
            Path("scheduled.fmu"),
            SimulationConfig(
                input_schedule=(
                    InputUpdate(0.1, {"command": 2.0}),
                    InputUpdate(0.2, {"vector": (1.0, 2.0)}),
                )
            ),
        )

        _, restored = read_configuration_payload(json.loads(json.dumps(payload)))

        self.assertEqual(
            restored.input_schedule,
            (
                InputUpdate(0.1, {"command": 2.0}),
                InputUpdate(0.2, {"vector": (1.0, 2.0)}),
            ),
        )


HAS_PYSIDE6 = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(HAS_PYSIDE6, "PySide6 is not installed")
class InputScheduleGuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def test_json_schedule_builds_public_input_updates(self) -> None:
        from gui.main import MainWindow

        window = MainWindow()
        metadata = ModelMetadata(
            model_id="scheduled",
            source_path="scheduled.fmu",
            fmi_version="3.0",
            model_name="Scheduled input model",
            interface_types=(InterfaceType.CO_SIMULATION,),
            executable_interface=InterfaceType.CO_SIMULATION,
            variables=(
                VariableMetadata("command", 1, "Float64", causality="input"),
                VariableMetadata("vector", 2, "Float64", causality="input", shape=(2,)),
                VariableMetadata("result", 3, "Float64", causality="output"),
            ),
        )
        try:
            window.current_metadata = metadata
            window._load_configuration_defaults(metadata)
            window.input_schedule_editor.setPlainText(
                '[{"time": 0.1, "values": {"command": 2.0}}, '
                '{"time": 0.2, "values": {"vector": [3, 4]}}]'
            )

            config = window._build_simulation_config()

            self.assertEqual(
                config.input_schedule,
                (
                    InputUpdate(0.1, {"command": 2.0}),
                    InputUpdate(0.2, {"vector": (3, 4)}),
                ),
            )
        finally:
            window.close()
            self.application.processEvents()

    def test_invalid_schedule_json_points_to_schedule_tab(self) -> None:
        from gui.main import MainWindow

        window = MainWindow()
        try:
            window.input_schedule_editor.setPlainText('{"time": 0.1}')

            with self.assertRaises(ValueError):
                window._input_schedule_from_editor()

            self.assertEqual(window.value_parse_error_tab, 3)
        finally:
            window.close()
            self.application.processEvents()
