"""Real-FMU regression checks for the single-FMU GUI workflow."""

from __future__ import annotations

import importlib.util
import time
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FMU_DIRECTORY = REPOSITORY_ROOT / "examples" / "fmus"
FMI2_VAN_DER_POL = FMU_DIRECTORY / "VanDerPol.fmu"
FMI3_VAN_DER_POL = FMU_DIRECTORY / "VanDerPol-fmi3.fmu"
HAS_PYSIDE6 = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(HAS_PYSIDE6, "PySide6 is not installed")
class GuiPublicWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def test_fmi2_van_der_pol_runs_through_the_gui(self) -> None:
        self._assert_complete_gui_workflow(FMI2_VAN_DER_POL)

    def test_fmi3_van_der_pol_uses_the_same_gui_workflow(self) -> None:
        self._assert_complete_gui_workflow(FMI3_VAN_DER_POL)

    def _assert_complete_gui_workflow(self, fmu_path: Path) -> None:
        if not fmu_path.is_file():
            self.skipTest(f"FMU fixture is unavailable: {fmu_path.name}")

        from gui.main import MainWindow

        window = MainWindow()
        try:
            self.assertTrue(window.load_fmu(fmu_path))
            self.assertEqual(window.fmu_list.count(), 1)
            self.assertTrue(window.fmu_canvas.has_model)
            self.assertTrue(window.configuration_action.isEnabled())
            self.assertEqual(window.workspace_splitter.handleWidth(), 8)
            self.assertEqual(window.main_splitter.handleWidth(), 8)
            self.assertTrue(window.main_splitter.isCollapsible(1))
            window.start_time_spin.setValue(0.0)
            window.stop_time_spin.setValue(0.02)
            window.step_size_spin.setValue(0.01)
            window.use_step_for_output_check.setChecked(False)
            window.output_interval_spin.setValue(0.02)
            window.validate_configuration()
            self.assertIsNotNone(window.current_config)
            self.assertEqual(window.current_config.output_interval, 0.02)

            window.run_simulation()
            self._wait_for_simulation(window)

            self.assertIsNotNone(window.current_result)
            self.assertTrue(window.current_result.successful)
            self.assertEqual(window.current_result.sample_count, 2)
            self.assertGreater(window.result_table.rowCount(), 0)
            self.assertGreater(window.statistics_table.rowCount(), 0)
            self.assertEqual(window.live_sample_count, window.current_result.sample_count)
            self.assertFalse(window.stop_action.isEnabled())
            self.assertIn("仿真完成", window.operation_log.toPlainText())
        finally:
            window.close()

    def _wait_for_simulation(self, window: object) -> None:
        deadline = time.monotonic() + 15.0
        while (
            getattr(window, "run_worker") is not None
            or getattr(window, "current_result") is None
        ) and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)

        self.application.processEvents()
        self.assertIsNone(getattr(window, "run_worker"), "GUI 仿真未在 15 秒内结束")
