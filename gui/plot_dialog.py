"""Matplotlib dialog for visualizing a completed Farcel public result."""

from __future__ import annotations

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout

from farcel.contracts import SimulationResult
from gui.presenter import scalar_plot_series


class ResultPlotDialog(QDialog):
    """Show numeric output series from one completed simulation result."""

    def __init__(self, result: SimulationResult, parent: object | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Farcel - 仿真结果曲线")
        self.resize(820, 520)

        layout = QVBoxLayout(self)
        figure = Figure(layout="constrained")
        canvas = FigureCanvas(figure)
        axes = figure.add_subplot(111)

        plotted_outputs = []
        skipped_outputs = []
        for name, timestamps, values in scalar_plot_series(result):
            try:
                numeric_values = tuple(float(value) for value in values)
            except (TypeError, ValueError):
                skipped_outputs.append(name)
                continue
            axes.plot(timestamps, numeric_values, linewidth=1.8, label=name)
            plotted_outputs.append(name)

        axes.set_title("Simulation result")
        axes.set_xlabel("Time")
        axes.set_ylabel("Output value")
        axes.grid(True, alpha=0.3)
        if plotted_outputs:
            axes.legend()
        else:
            axes.text(
                0.5,
                0.5,
                "No numeric output is available for plotting.",
                ha="center",
                va="center",
                transform=axes.transAxes,
            )

        if skipped_outputs:
            axes.text(
                0.01,
                0.01,
                "Skipped non-numeric outputs: " + ", ".join(skipped_outputs),
                ha="left",
                va="bottom",
                transform=axes.transAxes,
            )

        layout.addWidget(canvas)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
