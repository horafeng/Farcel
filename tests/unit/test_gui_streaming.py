"""Focused GUI checks for public progress, stop and result-chunk contracts."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is not installed")
class GuiStreamingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def test_worker_exposes_a_cooperative_public_stop_control(self) -> None:
        from farcel.contracts import SimulationConfig
        from gui.main import SimulationWorker

        worker = SimulationWorker(Path("example.fmu"), SimulationConfig())

        self.assertFalse(worker.control.stop_requested)
        worker.request_stop()
        self.assertTrue(worker.control.stop_requested)

    def test_window_appends_public_result_chunks_and_progress(self) -> None:
        from farcel.contracts import ResultChunk, RunProgress, SimulationState
        from gui.main import MainWindow

        window = MainWindow()
        try:
            window._prepare_live_results()
            window._show_run_progress(
                RunProgress(
                    start_time=0.0,
                    stop_time=1.0,
                    current_time=0.5,
                    completed_steps=5,
                    sample_count=3,
                    fraction=0.5,
                    state=SimulationState.RUNNING,
                )
            )
            window._show_result_chunk(
                ResultChunk(
                    run_id="run-1",
                    sequence=0,
                    time=(0.0, 0.5),
                    columns={"x0": (2.0, 1.0)},
                )
            )

            self.assertEqual(window.progress_bar.value(), 50)
            self.assertEqual(window.result_table.rowCount(), 2)
            self.assertEqual(window.result_table.columnCount(), 2)
            self.assertEqual(window.live_sample_count, 2)
            self.assertIn("最新 t=0.5", window.live_result_label.text())
        finally:
            window.close()

