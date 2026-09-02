"""Presentation behavior for advanced Phase 2 public contracts."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from farcel.contracts import (
    CapabilitySet,
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    SimulationResult,
    SimulationState,
)
from gui.presenter import scalar_plot_series


class ArrayPlotSeriesTest(unittest.TestCase):
    def test_array_output_expands_to_zero_based_element_series(self) -> None:
        result = SimulationResult(
            fmu_path="array.fmu",
            start_time=0.0,
            stop_time=0.1,
            step_size=0.1,
            completed_steps=1,
            final_time=0.1,
            completion_state=SimulationState.COMPLETED,
            timestamps=(0.0, 0.1),
            outputs={"y": ((1.0, 2.0), (3.0, 4.0))},
        )

        self.assertEqual(
            scalar_plot_series(result),
            (
                ("y[0]", (0.0, 0.1), (1.0, 3.0)),
                ("y[1]", (0.0, 0.1), (2.0, 4.0)),
            ),
        )


HAS_PYSIDE6 = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(HAS_PYSIDE6, "PySide6 is not installed")
class Phase2PresentationGuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def test_worker_keeps_the_selected_result_chunk_size(self) -> None:
        from gui.main import SimulationWorker

        worker = SimulationWorker(Path("example.fmu"), SimulationConfig(), 17)

        self.assertEqual(worker.result_chunk_size, 17)

    def test_non_grid_public_progress_is_logged_as_observed_early_return(self) -> None:
        from farcel.contracts import RunProgress
        from gui.main import MainWindow

        window = MainWindow()
        try:
            window.current_metadata = ModelMetadata(
                model_id="early-return",
                source_path="example.fmu",
                fmi_version="3.0",
                model_name="Early return model",
                interface_types=(InterfaceType.CO_SIMULATION,),
                executable_interface=InterfaceType.CO_SIMULATION,
                capabilities=CapabilitySet(
                    can_execute=True, supports_early_return=True
                ),
            )
            window.current_config = SimulationConfig(
                start_time=0.0, stop_time=1.0, communication_step=0.1
            )
            window._show_run_progress(
                RunProgress(
                    start_time=0.0,
                    stop_time=1.0,
                    current_time=0.15,
                    completed_steps=1,
                    sample_count=1,
                    fraction=0.15,
                    state=SimulationState.RUNNING,
                )
            )

            self.assertTrue(window.early_return_observed)
            self.assertIn("Early Return", window.progress_label.text())
            self.assertIn("Early Return", window.operation_log.toPlainText())
        finally:
            window.close()
            self.application.processEvents()
