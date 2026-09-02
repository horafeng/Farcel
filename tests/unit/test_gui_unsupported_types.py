"""GUI behavior for public runtime types not supported by Farcel yet."""

from __future__ import annotations

import importlib.util
import unittest

from farcel.contracts import InterfaceType, ModelMetadata, VariableMetadata


HAS_PYSIDE6 = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(HAS_PYSIDE6, "PySide6 is not installed")
class UnsupportedVariableTypeGuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def test_binary_and_clock_outputs_are_visible_but_not_selectable(self) -> None:
        from PySide6.QtCore import Qt

        from gui.main import MainWindow

        window = MainWindow()
        metadata = ModelMetadata(
            model_id="unsupported-types",
            source_path="example.fmu",
            fmi_version="3.0",
            model_name="Unsupported types",
            interface_types=(InterfaceType.CO_SIMULATION,),
            executable_interface=InterfaceType.CO_SIMULATION,
            variables=(
                VariableMetadata("value", 1, "Float64", causality="output"),
                VariableMetadata("payload", 2, "Binary", causality="output"),
                VariableMetadata("tick", 3, "Clock", causality="output"),
                VariableMetadata(
                    "incoming_payload", 4, "Binary", causality="input"
                ),
            ),
        )
        try:
            window.current_metadata = metadata
            window._load_configuration_defaults(metadata)
            output_items = {
                window.outputs_list.item(index).text(): window.outputs_list.item(index)
                for index in range(window.outputs_list.count())
            }

            self.assertEqual(output_items["value"].checkState(), Qt.Checked)
            self.assertEqual(output_items["payload"].checkState(), Qt.Unchecked)
            self.assertFalse(output_items["payload"].flags() & Qt.ItemIsEnabled)
            self.assertIn(
                "不能作为运行时输入或输出", output_items["payload"].toolTip()
            )
            self.assertFalse(output_items["tick"].flags() & Qt.ItemIsEnabled)
            self.assertNotIn("incoming_payload", window.initial_input_widgets)
        finally:
            window.close()
            self.application.processEvents()
