"""Checks for restoring a loaded FMU's GUI configuration defaults."""

from __future__ import annotations

import importlib.util
import unittest


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is not installed")
class ConfigurationResetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def test_restore_defaults_resets_time_values_and_requires_validation(self) -> None:
        from farcel.contracts import (
            DefaultExperiment,
            InterfaceType,
            ModelMetadata,
            VariableMetadata,
        )
        from gui.main import MainWindow

        metadata = ModelMetadata(
            model_id="example",
            source_path="example.fmu",
            fmi_version="2.0",
            model_name="Example",
            interface_types=(InterfaceType.CO_SIMULATION,),
            default_experiment=DefaultExperiment(
                start_time=2.0,
                stop_time=4.0,
                step_size=0.1,
            ),
            variables=(VariableMetadata("speed", 1, "Real", causality="output"),),
        )
        window = MainWindow()
        try:
            window.current_metadata = metadata
            window._load_configuration_defaults(metadata)
            window.start_time_spin.setValue(3.0)

            window.restore_configuration_defaults()

            self.assertEqual(window.start_time_spin.value(), 2.0)
            self.assertEqual(window.stop_time_spin.value(), 4.0)
            self.assertEqual(window.step_size_spin.value(), 0.1)
            self.assertIsNone(window.current_config)
            self.assertFalse(window.run_button.isEnabled())
        finally:
            window.close()

