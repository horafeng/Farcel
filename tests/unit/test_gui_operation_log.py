"""Focused checks for the GUI's local operation log."""

from __future__ import annotations

import importlib.util
import unittest


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is not installed")
class OperationLogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def test_operation_log_is_read_only_and_records_a_message(self) -> None:
        from gui.main import MainWindow

        window = MainWindow()
        try:
            window._append_operation_log("测试日志记录。")

            self.assertTrue(window.operation_log.isReadOnly())
            self.assertEqual(window.operation_log.maximumBlockCount(), 200)
            self.assertIn("测试日志记录。", window.operation_log.toPlainText())
        finally:
            window.close()

