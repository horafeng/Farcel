"""Farcel GUI milestone 1: select an FMU and inspect its public metadata."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QRectF, QThread, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QDoubleSpinBox,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from farcel import create_backend
from farcel.contracts import (
    EngineError,
    InputUpdate,
    ModelMetadata,
    ResultChunk,
    RunControl,
    RunProgress,
    SimulationConfig,
    SimulationResult,
    VariableMetadata,
)
from gui.presenter import (
    model_summary,
    input_variables,
    filtered_variables,
    parameter_variables,
    result_statistics,
    result_table_data,
    result_summary,
    runtime_channel_warning,
    simulation_defaults,
    validation_issue_messages,
    variable_rows,
    variable_detail_text,
    variable_table_row,
)
from gui.configuration_file import configuration_payload, read_configuration_payload


APP_STYLESHEET = """
QMainWindow {
    background: #F5F7F6;
    color: #1D2939;
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}
QToolBar {
    background: #FFFFFF;
    border: none;
    border-bottom: 1px solid #D9E2E0;
    padding: 6px 8px;
    spacing: 6px;
}
QToolButton {
    color: #3A625D;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 6px 10px;
}
QToolButton:hover {
    background: #EEF5F3;
    border-color: #C9DED8;
}
QGroupBox {
    background: #FFFFFF;
    border: 1px solid #D9E2E0;
    border-radius: 8px;
    margin-top: 12px;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
    color: #3A625D;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
}
QFrame {
    background: #FFFFFF;
    border: 1px solid #D9E2E0;
    border-radius: 8px;
}
QPushButton {
    background: #39736B;
    color: #FFFFFF;
    border: none;
    border-radius: 5px;
    min-height: 20px;
    padding: 5px 12px;
    font-weight: 600;
}
QPushButton:hover { background: #2E625B; }
QPushButton:pressed { background: #244E48; }
QPushButton:disabled {
    background: #DCE4E1;
    color: #7A8798;
}
QPushButton#secondaryButton {
    background: #EEF5F3;
    color: #3A625D;
    border: 1px solid #C9DED8;
}
QPushButton#secondaryButton:hover { background: #E0F0EC; }
QLineEdit, QDoubleSpinBox, QComboBox, QListWidget, QTableWidget, QTextEdit, QPlainTextEdit {
    background: #FFFFFF;
    border: 1px solid #D4DEDB;
    border-radius: 5px;
    padding: 3px;
    selection-background-color: #C8E1DC;
    selection-color: #23433F;
}
QLineEdit:focus, QDoubleSpinBox:focus, QComboBox:focus, QListWidget:focus,
QTableWidget:focus, QTextEdit:focus, QPlainTextEdit:focus {
    border: 1px solid #5F9D91;
}
QHeaderView::section {
    background: #F0F4F3;
    color: #45605C;
    border: none;
    border-right: 1px solid #D9E2E0;
    border-bottom: 1px solid #D9E2E0;
    padding: 5px;
    font-weight: 600;
}
QTabBar::tab {
    background: #F0F4F3;
    color: #5B6B82;
    border: 1px solid #D9E2E0;
    border-bottom: none;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    padding: 6px 13px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background: #FFFFFF;
    color: #3A625D;
    font-weight: 600;
}
QScrollBar:vertical {
    background: #F0F3F2;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #B7CBC5;
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #8BAAA2; }
QSplitter::handle {
    background: #E4EBE8;
}
QSplitter::handle:hover {
    background: #A9C9C1;
}
QSplitter::handle:horizontal {
    width: 8px;
    margin: 5px 1px;
}
QSplitter::handle:vertical {
    height: 8px;
    margin: 1px 5px;
}
QStatusBar {
    background: #FFFFFF;
    color: #52657E;
    border-top: 1px solid #D9E2E0;
}
"""


class FmuCanvasView(QGraphicsView):
    """A visual single-FMU workspace; it deliberately has no connection logic."""

    module_double_clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.canvas_scene = QGraphicsScene(self)
        self.setScene(self.canvas_scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setBackgroundBrush(QColor("#F7F9F8"))
        self.setFrameShape(QFrame.NoFrame)
        self._has_module = False
        self._show_empty_state()

    def show_model(self, metadata: ModelMetadata) -> None:
        """Draw one loaded FMU as a module in the centre of the canvas."""
        self.canvas_scene.clear()
        self._has_module = True
        self.canvas_scene.setSceneRect(-420, -260, 840, 520)

        module_path = QPainterPath()
        module_path.addRoundedRect(QRectF(-210, -110, 420, 220), 14, 14)
        module_pen = QPen(QColor("#3F8278"), 2)
        self.canvas_scene.addPath(module_path, module_pen, QBrush(QColor("#FFFFFF")))
        self.canvas_scene.addRect(
            QRectF(-210, -110, 420, 48),
            QPen(Qt.NoPen),
            QBrush(QColor("#E7F3F0")),
        )
        self.canvas_scene.addLine(-210, -62, 210, -62, QPen(QColor("#C7E0DB")))

        title = self.canvas_scene.addText(metadata.model_name)
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setDefaultTextColor(QColor("#2C615A"))
        title.setPos(-185, -100)

        details = self.canvas_scene.addText(
            f"FMU  ·  FMI {metadata.fmi_version}\n"
            "单 FMU 仿真模块\n\n"
            "双击模块打开仿真配置"
        )
        details.setDefaultTextColor(QColor("#5C6E6A"))
        details.setPos(-185, -42)

        self.fitInView(self.canvas_scene.sceneRect(), Qt.KeepAspectRatio)

    @property
    def has_model(self) -> bool:
        """Whether the workspace currently displays an active FMU module."""
        return self._has_module

    def _show_empty_state(self) -> None:
        self.canvas_scene.clear()
        self._has_module = False
        self.canvas_scene.setSceneRect(-420, -260, 840, 520)
        empty_text = self.canvas_scene.addText("从左侧导入或选择一个 FMU，开始搭建单模型仿真。")
        empty_text.setDefaultTextColor(QColor("#7B8D89"))
        empty_text.setPos(-175, -10)

    def mouseDoubleClickEvent(self, event: object) -> None:
        if self._has_module:
            self.module_double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)
        self.fitInView(self.canvas_scene.sceneRect(), Qt.KeepAspectRatio)


class SimulationWorker(QThread):
    """Run a single FMU outside the Qt event-loop thread."""

    succeeded = Signal(object)
    failed = Signal(object)
    progressed = Signal(object)
    result_chunk_received = Signal(object)

    def __init__(
        self, fmu_path: Path, config: SimulationConfig, result_chunk_size: int = 64
    ) -> None:
        super().__init__()
        self.fmu_path = fmu_path
        self.config = config
        self.result_chunk_size = result_chunk_size
        self.control = RunControl()

    def request_stop(self) -> None:
        """Request cooperative stopping through Farcel's public RunControl."""
        self.control.request_stop()

    def run(self) -> None:
        try:
            result = create_backend().run_fmu(
                self.fmu_path,
                self.config,
                control=self.control,
                on_progress=self.progressed.emit,
                on_result_chunk=self.result_chunk_received.emit,
                result_chunk_size=self.result_chunk_size,
            )
        except Exception as error:
            self.failed.emit(error)
            return
        self.succeeded.emit(result)


class MainWindow(QMainWindow):
    """Inspect one FMU using Farcel's public backend API."""

    MAX_VISIBLE_RESULT_ROWS = 4
    FLOAT_INPUT_TYPES = {"real", "float32", "float64"}
    INTEGER_INPUT_TYPES = {
        "integer",
        "enumeration",
        "int8",
        "uint8",
        "int16",
        "uint16",
        "int32",
        "uint32",
        "int64",
        "uint64",
    }

    def __init__(self) -> None:
        super().__init__()
        self.backend = create_backend()
        self.current_path: Path | None = None
        self.current_metadata: ModelMetadata | None = None
        self.current_config: SimulationConfig | None = None
        self.current_result: SimulationResult | None = None
        self.run_worker: SimulationWorker | None = None
        self.initial_input_widgets: dict[str, tuple[str, QWidget]] = {}
        self.parameter_widgets: dict[str, tuple[str, QWidget]] = {}
        self.value_parse_error_tab: int | None = None
        self.visible_variables: tuple[VariableMetadata, ...] = ()
        self.live_run_id: str | None = None
        self.next_chunk_sequence = 0
        self.live_sample_count = 0
        self.early_return_observed = False

        self.setWindowTitle("Farcel - FMU 仿真工作台")
        self.resize(1360, 860)
        self._create_toolbar()
        self._create_main_area()
        self.statusBar().showMessage("请选择一个 FMU 文件")
        self._append_operation_log("Farcel 已启动，等待选择 FMU 文件。")

    def _create_toolbar(self) -> None:
        toolbar = QToolBar("工具栏")
        toolbar.setMovable(False)
        self.import_action = toolbar.addAction("导入 FMU", self.choose_fmu)
        self.configuration_action = toolbar.addAction("仿真配置", self.open_configuration_dialog)
        self.validate_action = toolbar.addAction("验证配置", self.validate_configuration)
        self.run_action = toolbar.addAction("运行仿真", self.run_simulation)
        self.stop_action = toolbar.addAction("停止仿真", self.stop_simulation)
        toolbar.addSeparator()
        toolbar.addAction("保存配置", self.save_configuration)
        toolbar.addAction("载入配置", self.load_configuration)
        toolbar.addSeparator()
        self.plot_action = toolbar.addAction("显示曲线", self.show_result_plot)
        self.export_action = toolbar.addAction("导出 CSV", self.export_result_to_csv)
        self.configuration_action.setEnabled(False)
        self.validate_action.setEnabled(False)
        self.run_action.setEnabled(False)
        self.stop_action.setEnabled(False)
        self.plot_action.setEnabled(False)
        self.export_action.setEnabled(False)
        self.addToolBar(toolbar)

    def _create_main_area(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 10, 10, 10)

        self.workspace_splitter = QSplitter(Qt.Horizontal)
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.setHandleWidth(8)
        workspace = self.workspace_splitter

        library_group = QGroupBox("FMU 模型库")
        library_layout = QVBoxLayout(library_group)
        library_hint = QLabel("当前仅支持单 FMU 运行；选择列表项即可切换当前模型。")
        library_hint.setWordWrap(True)
        library_layout.addWidget(library_hint)
        self.fmu_list = QListWidget()
        self.fmu_list.setToolTip("双击或选择 FMU 可设为当前仿真模型。")
        self.fmu_list.currentItemChanged.connect(self._select_fmu_from_library)
        library_layout.addWidget(self.fmu_list, 1)
        import_button = QPushButton("导入 FMU")
        import_button.clicked.connect(self.choose_fmu)
        library_layout.addWidget(import_button)
        workspace.addWidget(library_group)

        canvas_group = QGroupBox("仿真画布")
        canvas_layout = QVBoxLayout(canvas_group)
        canvas_hint = QLabel("双击 FMU 模块可打开仿真配置。连线与多 FMU 协同仿真将在后端支持后加入。")
        canvas_hint.setWordWrap(True)
        canvas_layout.addWidget(canvas_hint)
        self.fmu_canvas = FmuCanvasView()
        self.fmu_canvas.module_double_clicked.connect(self.open_configuration_dialog)
        canvas_layout.addWidget(self.fmu_canvas, 1)
        workspace.addWidget(canvas_group)

        inspector_tabs = QTabWidget()
        properties_page = QWidget()
        properties_layout = QVBoxLayout(properties_page)
        self.path_label = QLabel("尚未选择 FMU 文件")
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        properties_layout.addWidget(self.path_label)
        details_frame = QFrame()
        details_frame.setFrameShape(QFrame.StyledPanel)
        self.details_layout = QFormLayout(details_frame)
        self.details_layout.addRow("模型信息", QLabel("选择文件后显示后端解析结果。"))
        properties_layout.addWidget(details_frame)
        properties_layout.addWidget(QLabel("诊断信息"))
        self.diagnostics_label = QLabel("无")
        self.diagnostics_label.setWordWrap(True)
        properties_layout.addWidget(self.diagnostics_label)
        properties_layout.addStretch()
        inspector_tabs.addTab(properties_page, "模型属性")

        variables_page = QWidget()
        variables_layout = QVBoxLayout(variables_page)
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("搜索"))
        self.variable_search = QLineEdit()
        self.variable_search.setPlaceholderText("按变量名称筛选")
        self.variable_search.textChanged.connect(self._refresh_variable_table)
        filter_layout.addWidget(self.variable_search, 1)
        filter_layout.addWidget(QLabel("方向"))
        self.causality_filter = QComboBox()
        self.causality_filter.addItem("全部", "all")
        self.causality_filter.addItem("输入", "input")
        self.causality_filter.addItem("参数", "parameter")
        self.causality_filter.addItem("输出", "output")
        self.causality_filter.addItem("其他", "other")
        self.causality_filter.currentIndexChanged.connect(self._refresh_variable_table)
        filter_layout.addWidget(self.causality_filter)
        variables_layout.addLayout(filter_layout)

        variables_content = QSplitter(Qt.Horizontal)
        variables_content.setChildrenCollapsible(False)
        variables_content.setHandleWidth(8)
        self.variables_table = QTableWidget(0, 7)
        self.variables_table.setHorizontalHeaderLabels(
            ["名称", "类型", "方向", "初始值", "最小值", "最大值", "单位"]
        )
        self.variables_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.variables_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.variables_table.horizontalHeader().setStretchLastSection(True)
        self.variables_table.itemSelectionChanged.connect(self._show_selected_variable_details)
        variables_content.addWidget(self.variables_table)

        variable_detail_group = QGroupBox("选中变量详情")
        variable_detail_layout = QVBoxLayout(variable_detail_group)
        self.variable_detail = QTextEdit("从变量列表中选择一项以查看详情。")
        self.variable_detail.setReadOnly(True)
        variable_detail_layout.addWidget(self.variable_detail)
        variables_content.addWidget(variable_detail_group)
        variables_content.setStretchFactor(0, 3)
        variables_content.setStretchFactor(1, 2)
        variables_layout.addWidget(variables_content, 1)
        inspector_tabs.addTab(variables_page, "变量浏览")
        workspace.addWidget(inspector_tabs)
        workspace.setStretchFactor(0, 1)
        workspace.setStretchFactor(1, 4)
        workspace.setStretchFactor(2, 2)

        bottom_tabs = QTabWidget()
        results_page = QWidget()
        results_layout = QVBoxLayout(results_page)
        results_layout.setContentsMargins(4, 4, 4, 4)
        self._create_result_area(results_layout)
        bottom_tabs.addTab(results_page, "运行结果")

        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        self.operation_log = QPlainTextEdit()
        self.operation_log.setReadOnly(True)
        self.operation_log.setMaximumBlockCount(200)
        self.operation_log.setPlaceholderText("导入、验证、运行与导出记录将显示在这里。")
        log_layout.addWidget(self.operation_log)
        bottom_tabs.addTab(log_page, "操作日志")

        self.main_splitter = QSplitter(Qt.Vertical)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(8)
        self.main_splitter.addWidget(workspace)
        self.main_splitter.addWidget(bottom_tabs)
        self.main_splitter.setStretchFactor(0, 3)
        self.main_splitter.setStretchFactor(1, 2)
        self.main_splitter.setCollapsible(1, True)
        self.main_splitter.setSizes([650, 170])
        layout.addWidget(self.main_splitter, 1)

        self._create_configuration_dialog()

        self.setCentralWidget(root)

    def _create_configuration_dialog(self) -> None:
        """Keep detailed simulation settings out of the main canvas workspace."""
        self.configuration_dialog = QDialog(self)
        self.configuration_dialog.setWindowTitle("仿真配置")
        self.configuration_dialog.setMinimumSize(860, 360)
        self.configuration_dialog.setSizeGripEnabled(True)
        dialog_layout = QVBoxLayout(self.configuration_dialog)
        self._create_configuration_area(dialog_layout)
        close_buttons = QDialogButtonBox(QDialogButtonBox.Close)
        close_buttons.rejected.connect(self.configuration_dialog.close)
        dialog_layout.addWidget(close_buttons)

    def _create_configuration_area(self, parent_layout: QVBoxLayout) -> None:
        """Create the single-FMU run configuration widgets."""
        self.configuration_group = QGroupBox("仿真配置")
        self.configuration_group.setEnabled(False)
        configuration_layout = QHBoxLayout(self.configuration_group)

        time_layout = QFormLayout()
        self.start_time_spin = self._create_time_spinbox()
        self.stop_time_spin = self._create_time_spinbox()
        self.step_size_spin = self._create_time_spinbox(minimum=0.000001)
        self.output_interval_spin = self._create_time_spinbox(minimum=0.000001)
        self.use_step_for_output_check = QCheckBox("结果采样间隔使用通信步长")
        self.start_time_spin.valueChanged.connect(self._mark_configuration_changed)
        self.stop_time_spin.valueChanged.connect(self._mark_configuration_changed)
        self.step_size_spin.valueChanged.connect(self._mark_configuration_changed)
        self.step_size_spin.valueChanged.connect(self._sync_output_interval_with_step)
        self.output_interval_spin.valueChanged.connect(self._mark_configuration_changed)
        self.use_step_for_output_check.toggled.connect(self._set_output_interval_mode)
        time_layout.addRow("开始时间", self.start_time_spin)
        time_layout.addRow("结束时间", self.stop_time_spin)
        time_layout.addRow("通信步长", self.step_size_spin)
        time_layout.addRow(self.use_step_for_output_check)
        time_layout.addRow("结果采样间隔", self.output_interval_spin)
        self.result_chunk_size_spin = QSpinBox()
        self.result_chunk_size_spin.setRange(1, 10_000)
        self.result_chunk_size_spin.setValue(64)
        self.result_chunk_size_spin.setToolTip(
            "每次推送到界面的实时结果采样点数量；只影响界面刷新频率，不改变最终结果。"
        )
        time_layout.addRow("实时结果块大小", self.result_chunk_size_spin)
        self.use_step_for_output_check.setChecked(True)
        configuration_layout.addLayout(time_layout)

        variable_tabs = QTabWidget()
        outputs_page = QWidget()
        outputs_layout = QVBoxLayout(outputs_page)
        self.outputs_list = QListWidget()
        self.outputs_list.setToolTip("勾选希望在仿真结果中采样的输出变量。")
        self.outputs_list.itemChanged.connect(self._mark_configuration_changed)
        outputs_layout.addWidget(self.outputs_list)
        variable_tabs.addTab(outputs_page, "输出变量")

        inputs_page = QWidget()
        inputs_layout = QVBoxLayout(inputs_page)
        initial_inputs_container = QWidget()
        self.initial_inputs_form = QFormLayout(initial_inputs_container)
        self.initial_inputs_scroll = QScrollArea()
        self.initial_inputs_scroll.setWidgetResizable(True)
        self.initial_inputs_scroll.setWidget(initial_inputs_container)
        inputs_layout.addWidget(self.initial_inputs_scroll)
        variable_tabs.addTab(inputs_page, "初始输入")

        parameters_page = QWidget()
        parameters_layout = QVBoxLayout(parameters_page)
        parameters_container = QWidget()
        self.parameters_form = QFormLayout(parameters_container)
        self.parameters_scroll = QScrollArea()
        self.parameters_scroll.setWidgetResizable(True)
        self.parameters_scroll.setWidget(parameters_container)
        parameters_layout.addWidget(self.parameters_scroll)
        variable_tabs.addTab(parameters_page, "参数")

        schedule_page = QWidget()
        schedule_layout = QVBoxLayout(schedule_page)
        schedule_hint = QLabel(
            "可选：在通信点改变输入。填写 JSON 数组；时间必须严格递增，"
            "具体通信点和变量值由后端验证。"
        )
        schedule_hint.setWordWrap(True)
        schedule_layout.addWidget(schedule_hint)
        self.input_schedule_editor = QPlainTextEdit()
        self.input_schedule_editor.setPlaceholderText(
            '[\n  {"time": 1.0, "values": {"u": 2.0}},\n'
            '  {"time": 2.0, "values": {"u": 3.0}}\n]'
        )
        self.input_schedule_editor.textChanged.connect(self._mark_configuration_changed)
        schedule_layout.addWidget(self.input_schedule_editor)
        variable_tabs.addTab(schedule_page, "时变输入")

        variable_tabs.setToolTip("初始输入和参数留空时均使用 FMU 默认值；时变输入按通信点应用。")
        variable_tabs.setMaximumHeight(145)
        self.variable_tabs = variable_tabs
        configuration_layout.addWidget(variable_tabs, 1)

        action_layout = QVBoxLayout()
        reset_configuration_button = QPushButton("恢复默认配置")
        reset_configuration_button.setObjectName("secondaryButton")
        reset_configuration_button.clicked.connect(self.restore_configuration_defaults)
        action_layout.addWidget(reset_configuration_button)
        validate_button = QPushButton("验证配置")
        validate_button.clicked.connect(self.validate_configuration)
        action_layout.addWidget(validate_button)
        self.run_button = QPushButton("运行仿真")
        self.run_button.setEnabled(False)
        self.run_button.clicked.connect(self.run_simulation)
        action_layout.addWidget(self.run_button)
        self.stop_button = QPushButton("停止仿真")
        self.stop_button.setObjectName("secondaryButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_simulation)
        action_layout.addWidget(self.stop_button)
        self.configuration_summary = QLabel("尚未生成仿真配置")
        self.configuration_summary.setWordWrap(True)
        action_layout.addWidget(self.configuration_summary)
        self.validation_label = QLabel()
        self.validation_label.setWordWrap(True)
        self.validation_label.setStyleSheet("color: #B03A2E;")
        action_layout.addWidget(self.validation_label)
        action_layout.addStretch()
        configuration_layout.addLayout(action_layout)

        parent_layout.addWidget(self.configuration_group)

    def _create_result_area(self, parent_layout: QVBoxLayout) -> None:
        """Create a read-only summary and sample table for the latest result."""
        result_group = QGroupBox("运行结果摘要")
        result_group.setMinimumHeight(0)
        result_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Ignored)
        result_group_layout = QVBoxLayout(result_group)
        result_group_layout.setContentsMargins(8, 10, 8, 6)
        self.result_layout = QFormLayout()
        self.result_layout.addRow("状态", QLabel("尚未运行仿真。"))
        result_summary_layout = QHBoxLayout()
        result_summary_layout.addLayout(self.result_layout, 1)
        result_actions_layout = QVBoxLayout()
        self.plot_button = QPushButton("显示曲线")
        self.plot_button.setObjectName("secondaryButton")
        self.plot_button.setEnabled(False)
        self.plot_button.clicked.connect(self.show_result_plot)
        result_actions_layout.addWidget(self.plot_button)
        self.export_button = QPushButton("导出 CSV")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_result_to_csv)
        result_actions_layout.addWidget(self.export_button)
        result_actions_layout.addStretch()
        result_summary_layout.addLayout(result_actions_layout)
        result_group_layout.addLayout(result_summary_layout)

        progress_layout = QHBoxLayout()
        self.progress_label = QLabel("尚未运行")
        progress_layout.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        progress_layout.addWidget(self.progress_bar, 1)
        self.live_result_label = QLabel("实时采样：等待仿真开始")
        self.live_result_label.setWordWrap(True)
        progress_layout.addWidget(self.live_result_label, 2)
        result_group_layout.addLayout(progress_layout)

        self.result_tabs = QTabWidget()
        self.result_tabs.setMinimumHeight(0)
        self.result_tabs.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Ignored)
        self.result_table = QTableWidget(0, 0)
        self.result_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.result_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.result_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.result_tabs.addTab(self.result_table, "采样数据")

        self.statistics_table = QTableWidget(0, 5)
        self.statistics_table.setHorizontalHeaderLabels(
            ["变量", "最小值", "最大值", "平均值", "最终值"]
        )
        self.statistics_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.statistics_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.statistics_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.result_tabs.addTab(self.statistics_table, "结果统计")
        self.result_tabs.setVisible(False)
        result_group_layout.addWidget(self.result_tabs)
        parent_layout.addWidget(result_group)

    @staticmethod
    def _create_time_spinbox(minimum: float = -1_000_000.0) -> QDoubleSpinBox:
        spinbox = QDoubleSpinBox()
        spinbox.setRange(minimum, 1_000_000.0)
        spinbox.setDecimals(6)
        spinbox.setSingleStep(0.01)
        return spinbox

    def choose_fmu(self) -> None:
        examples_directory = Path.cwd() / "examples" / "fmus"
        start_directory = str(examples_directory if examples_directory.is_dir() else Path.cwd())
        selected_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 FMU 文件",
            start_directory,
            "FMU 文件 (*.fmu)",
        )
        if selected_path:
            self.load_fmu(Path(selected_path))

    def _select_fmu_from_library(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None = None,
    ) -> None:
        """Switch the active single-FMU workspace model from the left library."""
        if current is None:
            return
        selected_path = current.data(Qt.UserRole)
        if selected_path and Path(selected_path) != self.current_path:
            self.load_fmu(Path(selected_path), add_to_library=False)

    def _add_fmu_to_library(self, path: Path) -> None:
        """Remember selectable FMUs without treating them as a coupled simulation."""
        normalized_path = str(path.resolve())
        for index in range(self.fmu_list.count()):
            item = self.fmu_list.item(index)
            if item.data(Qt.UserRole) == normalized_path:
                self.fmu_list.blockSignals(True)
                self.fmu_list.setCurrentItem(item)
                self.fmu_list.blockSignals(False)
                return

        item = QListWidgetItem(path.name)
        item.setToolTip(normalized_path)
        item.setData(Qt.UserRole, normalized_path)
        self.fmu_list.blockSignals(True)
        self.fmu_list.addItem(item)
        self.fmu_list.setCurrentItem(item)
        self.fmu_list.blockSignals(False)

    def open_configuration_dialog(self) -> None:
        """Open detailed settings from the toolbar or by double-clicking the module."""
        if self.current_metadata is None:
            QMessageBox.information(self, "尚未选择 FMU", "请先导入或选择一个 FMU 文件。")
            return
        self.configuration_dialog.show()
        self.configuration_dialog.raise_()
        self.configuration_dialog.activateWindow()

    def load_fmu(self, path: Path, *, add_to_library: bool = True) -> bool:
        """Load metadata through the public engine boundary only."""
        try:
            metadata = self.backend.load_fmu(path)
        except EngineError as error:
            self._show_engine_error(error)
            return False

        self.current_path = path
        self.current_metadata = metadata
        self.current_config = None
        self.current_result = None
        self.run_button.setEnabled(False)
        self.run_action.setEnabled(False)
        self.configuration_action.setEnabled(True)
        self.validate_action.setEnabled(True)
        self.path_label.setText(str(path))
        self._show_metadata(metadata)
        self.fmu_canvas.show_model(metadata)
        if add_to_library:
            self._add_fmu_to_library(path)
        self._append_operation_log(
            f"已导入 FMU：{path.name}（模型：{metadata.model_name}）。"
        )
        self.statusBar().showMessage("FMU 元数据加载成功", 3000)
        return True

    def _show_metadata(self, metadata: ModelMetadata) -> None:
        while self.details_layout.rowCount():
            self.details_layout.removeRow(0)
        for label, value in model_summary(metadata):
            self.details_layout.addRow(label, QLabel(value))

        self._refresh_variable_table()
        self.diagnostics_label.setText("\n".join(metadata.diagnostics) or "无")
        self._load_configuration_defaults(metadata)

    def _refresh_variable_table(self, _value: object | None = None) -> None:
        """Apply name and causality filters to the current public metadata."""
        if self.current_metadata is None:
            return

        category = self.causality_filter.currentData() or "all"
        self.visible_variables = filtered_variables(
            self.current_metadata,
            self.variable_search.text(),
            category,
        )
        self.variables_table.clearContents()
        self.variables_table.setRowCount(len(self.visible_variables))
        for row_index, variable in enumerate(self.visible_variables):
            for column_index, value in enumerate(variable_table_row(variable)):
                self.variables_table.setItem(
                    row_index,
                    column_index,
                    QTableWidgetItem(value),
                )
        self.variables_table.resizeColumnsToContents()
        self.variable_detail.setPlainText("从变量列表中选择一项以查看详情。")

    def _show_selected_variable_details(self) -> None:
        """Show the public metadata for the row currently selected in the table."""
        selected_rows = self.variables_table.selectionModel().selectedRows()
        if not selected_rows:
            return
        row_index = selected_rows[0].row()
        if 0 <= row_index < len(self.visible_variables):
            self.variable_detail.setPlainText(
                variable_detail_text(self.visible_variables[row_index])
            )

    def _load_configuration_defaults(self, metadata: ModelMetadata) -> None:
        """Populate config controls from the actual metadata, never hard-coded FMU data."""
        self.current_config = None
        self.run_button.setEnabled(False)
        self.run_action.setEnabled(False)
        start_time, stop_time, step_size = simulation_defaults(metadata)
        self.start_time_spin.setValue(start_time)
        self.stop_time_spin.setValue(stop_time)
        self.step_size_spin.setValue(step_size)
        self.output_interval_spin.setValue(step_size)
        self.use_step_for_output_check.setChecked(True)
        self.input_schedule_editor.clear()

        self.outputs_list.clear()
        for variable in metadata.variables:
            if variable.causality != "output":
                continue
            item = QListWidgetItem(variable.name)
            warning = runtime_channel_warning(variable)
            if warning is None:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
            else:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                item.setCheckState(Qt.Unchecked)
                item.setToolTip(warning)
                item.setForeground(QColor("#7B8582"))
            self.outputs_list.addItem(item)

        self._load_initial_input_controls(metadata)
        self._load_parameter_controls(metadata)

        self.configuration_group.setEnabled(True)
        self.configuration_summary.setText("已按 FMU 元数据填充默认配置。")
        self._clear_validation_feedback()
        self._reset_result_summary()

    def restore_configuration_defaults(self) -> None:
        """Restore one loaded FMU's declared defaults and require validation again."""
        if self.current_metadata is None:
            return

        self._load_configuration_defaults(self.current_metadata)
        self.configuration_summary.setText("已恢复 FMU 默认配置，请验证后运行。")
        self.statusBar().showMessage("已恢复 FMU 默认配置。", 3000)
        self._append_operation_log("已恢复 FMU 默认配置，等待重新验证。")

    def _build_simulation_config(self) -> SimulationConfig:
        """Build the public configuration object from the current GUI controls."""
        selected_outputs = tuple(
            self.outputs_list.item(index).text()
            for index in range(self.outputs_list.count())
            if self.outputs_list.item(index).checkState() == Qt.Checked
        )
        return SimulationConfig(
            start_time=self.start_time_spin.value(),
            stop_time=self.stop_time_spin.value(),
            communication_step=self.step_size_spin.value(),
            output_interval=(
                None
                if self.use_step_for_output_check.isChecked()
                else self.output_interval_spin.value()
            ),
            parameters=self._parameters_from_controls(),
            initial_inputs=self._initial_inputs_from_controls(),
            selected_outputs=selected_outputs,
            input_schedule=self._input_schedule_from_editor(),
        )

    def _set_output_interval_mode(self, uses_communication_step: bool) -> None:
        """Keep the default sampling behavior explicit without duplicating a value."""
        self.output_interval_spin.setEnabled(not uses_communication_step)
        if uses_communication_step:
            self.output_interval_spin.setValue(self.step_size_spin.value())
        self._mark_configuration_changed()

    def _sync_output_interval_with_step(self, step_size: float) -> None:
        """Reflect the effective default interval while the follow-step mode is active."""
        if self.use_step_for_output_check.isChecked():
            self.output_interval_spin.setValue(step_size)

    def _load_initial_input_controls(self, metadata: ModelMetadata) -> None:
        """Build minimal scalar initial-input controls from public metadata."""
        self._load_scalar_value_controls(
            self.initial_inputs_form,
            self.initial_input_widgets,
            input_variables(metadata),
            "此 FMU 没有可配置的标量输入变量。",
        )

    def _load_parameter_controls(self, metadata: ModelMetadata) -> None:
        """Build minimal scalar parameter controls from public metadata."""
        self._load_scalar_value_controls(
            self.parameters_form,
            self.parameter_widgets,
            parameter_variables(metadata),
            "此 FMU 没有可配置的标量参数。",
        )

    def _load_scalar_value_controls(
        self,
        form: QFormLayout,
        widgets: dict[str, tuple[str, QWidget]],
        variables: tuple[VariableMetadata, ...],
        empty_message: str,
    ) -> None:
        """Build optional scalar and array controls from public variable metadata."""
        while form.rowCount():
            form.removeRow(0)
        widgets.clear()

        supported_count = 0
        unsupported_variables: list[tuple[str, str]] = []
        for variable in variables:
            data_type = variable.data_type.casefold()
            if warning := runtime_channel_warning(variable):
                unsupported_variables.append((variable.name, warning))
                continue
            if data_type not in self.FLOAT_INPUT_TYPES | self.INTEGER_INPUT_TYPES | {"boolean", "string"}:
                continue

            label = variable.name
            if variable.unit:
                label = f"{label} ({variable.unit})"
            if variable.shape:
                widget = QPlainTextEdit()
                widget.setMinimumHeight(52)
                widget.setMaximumHeight(72)
                dimension_note = (
                    "；动态维度由结构参数覆盖后由后端验证"
                    if variable.dimension_value_references
                    else ""
                )
                widget.setPlaceholderText(
                    f"输入 JSON 数组，声明形状 {variable.shape}{dimension_note}；留空使用 FMU 默认值"
                )
                widget.textChanged.connect(self._mark_configuration_changed)
            elif data_type == "boolean":
                widget: QWidget = QComboBox()
                widget.addItem("使用 FMU 默认值", None)
                widget.addItem("True", True)
                widget.addItem("False", False)
                widget.currentIndexChanged.connect(self._mark_configuration_changed)
            else:
                widget = QLineEdit()
                placeholder = "留空则使用 FMU 默认值"
                if variable.start is not None:
                    placeholder = f"FMU 默认值：{variable.start}"
                widget.setPlaceholderText(placeholder)
                widget.textChanged.connect(self._mark_configuration_changed)

            tooltip_parts = [f"类型：{variable.data_type}"]
            if variable.shape:
                tooltip_parts.append(f"声明形状：{variable.shape}")
            if variable.dimension_value_references:
                tooltip_parts.append("含动态维度，最终形状由后端在验证时解析")
            if variable.minimum is not None or variable.maximum is not None:
                tooltip_parts.append(f"范围：{variable.minimum} ～ {variable.maximum}")
            if variable.description:
                tooltip_parts.append(variable.description)
            widget.setToolTip("\n".join(tooltip_parts))
            form.addRow(label, widget)
            widgets[variable.name] = (data_type, widget)
            supported_count += 1

        for name, warning in unsupported_variables:
            notice = QLabel(f"{name}: {warning}")
            notice.setStyleSheet("color: #7B4F00;")
            form.addRow(notice)
        if supported_count == 0:
            if not unsupported_variables:
                form.addRow(QLabel(empty_message))

    def _initial_inputs_from_controls(self) -> dict[str, object]:
        """Convert non-empty input controls to public SimulationConfig values."""
        return self._values_from_controls(self.initial_input_widgets, "输入", 1)

    def _parameters_from_controls(self) -> dict[str, object]:
        """Convert non-empty parameter controls to public SimulationConfig values."""
        return self._values_from_controls(self.parameter_widgets, "参数", 2)

    def _values_from_controls(
        self,
        widgets: dict[str, tuple[str, QWidget]],
        value_kind: str,
        tab_index: int,
    ) -> dict[str, object]:
        """Convert optional scalar widgets while preserving FMU defaults when blank."""
        values: dict[str, object] = {}
        for name, (data_type, widget) in widgets.items():
            if isinstance(widget, QComboBox):
                value = widget.currentData()
                if value is not None:
                    values[name] = value
                continue

            if isinstance(widget, QPlainTextEdit):
                raw_value = widget.toPlainText().strip()
                if not raw_value:
                    continue
                try:
                    parsed_value = json.loads(raw_value)
                    if not isinstance(parsed_value, list):
                        raise ValueError("array value must be a JSON list")
                    values[name] = self._json_array_to_tuple(parsed_value)
                except (json.JSONDecodeError, ValueError) as error:
                    self.value_parse_error_tab = tab_index
                    raise ValueError(
                        f"{value_kind}变量 {name} 必须是 JSON 数组。"
                    ) from error
                continue

            if not isinstance(widget, QLineEdit):
                continue
            raw_value = widget.text().strip()
            if not raw_value:
                continue
            try:
                if data_type in self.FLOAT_INPUT_TYPES:
                    values[name] = float(raw_value)
                elif data_type in self.INTEGER_INPUT_TYPES:
                    values[name] = int(raw_value, 10)
                else:
                    values[name] = raw_value
            except ValueError as error:
                self.value_parse_error_tab = tab_index
                raise ValueError(f"{value_kind}变量 {name} 的值格式无效。") from error
        return values

    @staticmethod
    def _json_array_to_tuple(value: object) -> object:
        """Convert JSON arrays to the nested tuple representation used at the public boundary."""
        if isinstance(value, list):
            return tuple(MainWindow._json_array_to_tuple(item) for item in value)
        return value

    def _input_schedule_from_editor(self) -> tuple[InputUpdate, ...]:
        """Decode optional JSON schedule text into public InputUpdate values."""
        raw_schedule = self.input_schedule_editor.toPlainText().strip()
        if not raw_schedule:
            return ()
        try:
            entries = json.loads(raw_schedule)
            if not isinstance(entries, list):
                raise ValueError("schedule must be a JSON list")

            updates = []
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("schedule entry must be an object")
                time = entry.get("time")
                values = entry.get("values")
                if isinstance(time, bool) or not isinstance(time, (int, float)):
                    raise ValueError("schedule time must be numeric")
                if not isinstance(values, dict) or not all(
                    isinstance(name, str) for name in values
                ):
                    raise ValueError("schedule values must map string names to values")
                updates.append(
                    InputUpdate(
                        float(time),
                        {
                            name: self._json_array_to_tuple(value)
                            for name, value in values.items()
                        },
                    )
                )
            return tuple(updates)
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            self.value_parse_error_tab = 3
            raise ValueError(
                "时变输入必须是由 time 和 values 组成的 JSON 数组。"
            ) from error

    @staticmethod
    def _input_schedule_text(schedule: tuple[InputUpdate, ...]) -> str:
        """Format public schedule updates for the editable JSON field."""
        if not schedule:
            return ""
        return json.dumps(
            [
                {"time": update.time, "values": dict(update.values)}
                for update in schedule
            ],
            ensure_ascii=False,
            indent=2,
        )

    def validate_configuration(self) -> None:
        """Validate configuration through the public backend API without running it."""
        if self.current_metadata is None:
            return

        self._clear_validation_feedback()
        self.value_parse_error_tab = None
        self._append_operation_log("开始验证仿真配置。")
        try:
            config = self._build_simulation_config()
        except ValueError as error:
            self.validation_label.setText(str(error))
            self.configuration_summary.setText("输入或参数格式无效，请修改后重新验证。")
            self.variable_tabs.setCurrentIndex(self.value_parse_error_tab or 1)
            self.variable_tabs.setStyleSheet("border: 1px solid #B03A2E;")
            self._append_operation_log(f"配置验证失败：{error}")
            return
        try:
            report = self.backend.validate_config(self.current_metadata, config)
        except EngineError as error:
            self._show_validation_error(error)
            return

        if not report.is_valid:
            self._show_validation_issues(
                tuple(
                    {"field": issue.field, "message": issue.message}
                    for issue in report.issues
                )
            )
            return

        self.current_config = config
        sampling_text = (
            "与通信步长相同"
            if self.current_config.output_interval is None
            else str(self.current_config.output_interval)
        )
        self.configuration_summary.setText(
            "配置验证通过："
            f"{self.current_config.start_time} → {self.current_config.stop_time}，"
            f"步长 {self.current_config.communication_step}，"
            f"采样间隔 {sampling_text}，"
            f"已选 {len(self.current_config.selected_outputs)} 个输出，"
            f"已设置 {len(self.current_config.initial_inputs)} 个初始输入，"
            f"已设置 {len(self.current_config.parameters)} 个参数，"
            f"已设置 {len(self.current_config.input_schedule)} 个时变输入点。"
        )
        self.statusBar().showMessage("仿真配置验证通过，已可运行。", 3000)
        self._append_operation_log("仿真配置验证通过。")
        self.run_button.setEnabled(True)
        self.run_action.setEnabled(True)

    def _mark_configuration_changed(self) -> None:
        """Require another validation after the user changes any run setting."""
        if self.current_config is None:
            return
        self.current_config = None
        self.run_button.setEnabled(False)
        self.run_action.setEnabled(False)
        self.configuration_summary.setText("配置已修改，请重新验证。")

    def run_simulation(self) -> None:
        """Start the synchronous public run API in a dedicated Qt worker thread."""
        if self.current_path is None or self.current_config is None:
            return
        if self.run_worker is not None and self.run_worker.isRunning():
            return

        self.run_button.setEnabled(False)
        self.run_action.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.stop_action.setEnabled(True)
        self.configuration_group.setEnabled(False)
        self.configuration_summary.setText("仿真运行中，请稍候……")
        self.statusBar().showMessage("仿真运行中……")
        self._append_operation_log(
            f"开始运行仿真，实时结果块大小 {self.result_chunk_size_spin.value()}。"
        )
        self._prepare_live_results()
        if self.current_metadata.capabilities.supports_event_mode:
            self._append_operation_log(
                "此 FMI 3 FMU 支持 Event Mode；Farcel 后端将自动处理事件模式。"
            )
        if self.current_metadata.capabilities.supports_early_return:
            self._append_operation_log(
                "此 FMI 3 FMU 支持 Early Return；界面将提示观测到的非通信点进度。"
            )

        self.run_worker = SimulationWorker(
            self.current_path,
            self.current_config,
            self.result_chunk_size_spin.value(),
        )
        self.run_worker.progressed.connect(self._show_run_progress, Qt.QueuedConnection)
        self.run_worker.result_chunk_received.connect(
            self._show_result_chunk,
            Qt.QueuedConnection,
        )
        self.run_worker.succeeded.connect(self._show_simulation_result)
        self.run_worker.failed.connect(self._show_simulation_failure)
        self.run_worker.finished.connect(self._finish_simulation)
        self.run_worker.start()

    def stop_simulation(self) -> None:
        """Request a cooperative stop; the current native doStep is not interrupted."""
        if self.run_worker is None or not self.run_worker.isRunning():
            return
        self.run_worker.request_stop()
        self.stop_button.setEnabled(False)
        self.stop_action.setEnabled(False)
        self.configuration_summary.setText("已请求停止，正在等待当前通信步完成。")
        self.statusBar().showMessage("已请求停止仿真……", 3000)
        self._append_operation_log("已请求停止仿真，等待当前通信步完成。")

    def _prepare_live_results(self) -> None:
        """Clear live-stream display state before one worker-owned backend run."""
        self.live_run_id = None
        self.next_chunk_sequence = 0
        self.live_sample_count = 0
        self.early_return_observed = False
        self.progress_bar.setValue(0)
        self.progress_label.setText("运行中：0%")
        self.live_result_label.setText(
            f"实时采样：等待首个结果块（每块 {self.result_chunk_size_spin.value()} 点）"
        )
        self.result_table.clearContents()
        self.result_table.setRowCount(0)
        self.result_table.setColumnCount(0)
        self.result_tabs.setCurrentIndex(0)
        self.result_tabs.setVisible(True)

    def _show_run_progress(self, progress: object) -> None:
        """Receive worker-thread progress through a Qt signal on the UI thread."""
        if not isinstance(progress, RunProgress):
            return
        percent = round(progress.fraction * 100)
        self.progress_bar.setValue(percent)
        early_return_text = ""
        if self._is_observed_early_return(progress):
            early_return_text = "，观测到 Early Return"
            if not self.early_return_observed:
                self.early_return_observed = True
                self._append_operation_log(
                    f"观测到 FMI 3 Early Return：t={progress.current_time:.6g} 不在通信点网格上；"
                    "后端会继续完成当前目标通信步。"
                )
        self.progress_label.setText(
            f"运行中：{percent}%（t={progress.current_time:.6g}{early_return_text}）"
        )

    def _is_observed_early_return(self, progress: RunProgress) -> bool:
        """Use only documented public progress semantics to identify a non-grid return."""
        if (
            self.current_metadata is None
            or self.current_config is None
            or not self.current_metadata.capabilities.supports_early_return
        ):
            return False
        step = self.current_config.communication_step
        if step <= 0:
            return False
        offset = (progress.current_time - self.current_config.start_time) / step
        tolerance = max(1e-9, abs(step) * 1e-9)
        return abs(offset - round(offset)) > tolerance

    def _show_result_chunk(self, chunk: object) -> None:
        """Append canonical public result samples received from the worker thread."""
        if not isinstance(chunk, ResultChunk) or not chunk.time:
            return
        if self.live_run_id is None:
            self.live_run_id = chunk.run_id
            self.next_chunk_sequence = 0
        if chunk.run_id != self.live_run_id or chunk.sequence != self.next_chunk_sequence:
            self._append_operation_log("已忽略顺序无效的实时结果块。")
            return

        headers = ("时间", *chunk.columns)
        if self.result_table.columnCount() == 0:
            self.result_table.setColumnCount(len(headers))
            self.result_table.setHorizontalHeaderLabels(headers)
        elif tuple(
            self.result_table.horizontalHeaderItem(index).text()
            for index in range(self.result_table.columnCount())
        ) != headers:
            self._append_operation_log("已忽略列结构不一致的实时结果块。")
            return

        first_row = self.result_table.rowCount()
        self.result_table.setRowCount(first_row + len(chunk.time))
        output_names = tuple(chunk.columns)
        for offset, timestamp in enumerate(chunk.time):
            self.result_table.setItem(first_row + offset, 0, QTableWidgetItem(str(timestamp)))
            for column_index, name in enumerate(output_names, start=1):
                self.result_table.setItem(
                    first_row + offset,
                    column_index,
                    QTableWidgetItem(str(chunk.columns[name][offset])),
                )
        self.live_sample_count += len(chunk.time)
        self.next_chunk_sequence += 1
        self.live_result_label.setText(
            f"实时采样：{self.live_sample_count} 点，最新 t={chunk.time[-1]:.6g}"
        )
        if chunk.final_chunk:
            self._append_operation_log("已接收最终实时结果块。")

    def _show_simulation_result(self, result: object) -> None:
        if not isinstance(result, SimulationResult):
            self._show_simulation_failure(RuntimeError("后端返回了无效的仿真结果。"))
            return
        self.current_result = result
        self._clear_result_rows()
        for label, value in result_summary(result):
            self.result_layout.addRow(label, QLabel(value))
        self._show_result_table(result)
        self._show_result_statistics(result)
        self.result_tabs.setVisible(True)
        self.plot_button.setEnabled(True)
        self.export_button.setEnabled(True)
        self.plot_action.setEnabled(True)
        self.export_action.setEnabled(True)
        self.progress_bar.setValue(100 if result.successful else self.progress_bar.value())
        self.progress_label.setText(
            "仿真完成" if result.successful else "仿真已停止"
        )
        self.live_result_label.setText(
            f"最终结果：{result.sample_count} 个采样点，t={result.final_time:.6g}"
        )
        self.configuration_summary.setText("仿真已完成，可查看结果曲线或导出 CSV。")
        self.statusBar().showMessage("仿真完成。", 3000)
        self._append_operation_log(
            f"仿真完成：{result.sample_count} 个采样点，最终时间 {result.final_time}。"
        )

    def _show_simulation_failure(self, error: object) -> None:
        if isinstance(error, EngineError):
            message = f"{error.code.value}：{error.message}"
        else:
            message = f"运行失败：{error}"
        self.configuration_summary.setText(message)
        self.statusBar().showMessage(message, 5000)
        self.progress_label.setText("仿真失败")
        self.live_result_label.setText("实时采样：运行未完成")
        self._append_operation_log(message)
        QMessageBox.critical(self, "仿真失败", message)

    def _finish_simulation(self) -> None:
        self.configuration_group.setEnabled(True)
        self.run_button.setEnabled(self.current_config is not None)
        self.run_action.setEnabled(self.current_config is not None)
        self.stop_button.setEnabled(False)
        self.stop_action.setEnabled(False)
        if self.run_worker is not None:
            self.run_worker.deleteLater()
        self.run_worker = None

    def _clear_result_rows(self) -> None:
        while self.result_layout.rowCount():
            self.result_layout.removeRow(0)

    def _reset_result_summary(self) -> None:
        self._clear_result_rows()
        self.result_layout.addRow("状态", QLabel("尚未运行仿真。"))
        self.result_table.clearContents()
        self.result_table.setRowCount(0)
        self.result_table.setColumnCount(0)
        self.result_table.setMinimumHeight(0)
        self.result_table.setMaximumHeight(16_777_215)
        self.statistics_table.clearContents()
        self.statistics_table.setRowCount(0)
        self.result_tabs.setVisible(False)
        self.progress_bar.setValue(0)
        self.progress_label.setText("尚未运行")
        self.live_result_label.setText("实时采样：等待仿真开始")
        self.plot_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.plot_action.setEnabled(False)
        self.export_action.setEnabled(False)

    def _show_result_table(self, result: SimulationResult) -> None:
        """Display timestamped samples returned by the public result contract."""
        headers, rows = result_table_data(result)
        self.result_table.setColumnCount(len(headers))
        self.result_table.setHorizontalHeaderLabels(headers)
        self.result_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                self.result_table.setItem(
                    row_index,
                    column_index,
                    QTableWidgetItem(value),
                )
        self.result_table.resizeRowsToContents()
        visible_row_count = min(len(rows), self.MAX_VISIBLE_RESULT_ROWS)
        table_height = (
            self.result_table.horizontalHeader().height()
            + sum(
                self.result_table.rowHeight(row_index)
                for row_index in range(visible_row_count)
            )
            + self.result_table.frameWidth() * 2
        )
        self.result_table.setMinimumHeight(min(table_height, 86))
        self.result_table.setMaximumHeight(table_height)

    def _show_result_statistics(self, result: SimulationResult) -> None:
        """Display output statistics derived only from the public result contract."""
        rows = result_statistics(result)
        self.statistics_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                self.statistics_table.setItem(
                    row_index,
                    column_index,
                    QTableWidgetItem(value),
                )
        self.statistics_table.resizeRowsToContents()

    def show_result_plot(self) -> None:
        """Open a plot for the latest result without re-running the FMU."""
        if self.current_result is None:
            return

        try:
            from gui.plot_dialog import ResultPlotDialog
        except ImportError:
            QMessageBox.information(
                self,
                "缺少曲线组件",
                '请在虚拟环境中执行：py -m pip install -e ".[gui]"',
            )
            return

        ResultPlotDialog(self.current_result, self).exec()

    def save_configuration(self) -> None:
        """Save the current single-FMU GUI configuration as JSON."""
        if self.current_path is None:
            QMessageBox.information(self, "无法保存配置", "请先选择一个 FMU 文件。")
            return
        try:
            config = self._build_simulation_config()
        except ValueError as error:
            QMessageBox.warning(self, "无法保存配置", str(error))
            return

        suggested_path = Path.cwd() / "artifacts" / f"{self.current_path.stem}.farcel.json"
        selected_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存仿真配置",
            str(suggested_path),
            "Farcel 配置 (*.farcel.json);;JSON 文件 (*.json)",
        )
        if not selected_path:
            return

        destination = self._configuration_file_path(Path(selected_path))
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(
                    configuration_payload(self.current_path, config),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as error:
            self._append_operation_log(f"保存配置失败：{error}")
            QMessageBox.critical(self, "保存配置失败", str(error))
            return

        message = f"配置已保存：{destination}"
        self.statusBar().showMessage(message, 5000)
        self._append_operation_log(message)
        QMessageBox.information(self, "保存配置成功", message)

    def load_configuration(self) -> None:
        """Load a saved GUI configuration and require fresh backend validation."""
        selected_path, _ = QFileDialog.getOpenFileName(
            self,
            "载入仿真配置",
            str(Path.cwd()),
            "Farcel 配置 (*.farcel.json);;JSON 文件 (*.json)",
        )
        if not selected_path:
            return

        try:
            payload = json.loads(Path(selected_path).read_text(encoding="utf-8"))
            fmu_path, config = read_configuration_payload(payload)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            self._append_operation_log(f"载入配置失败：{error}")
            QMessageBox.critical(self, "载入配置失败", str(error))
            return

        if not self.load_fmu(fmu_path):
            return
        if not self._configuration_matches_loaded_fmu(config):
            return

        self.start_time_spin.setValue(config.start_time)
        self.stop_time_spin.setValue(config.stop_time)
        self.step_size_spin.setValue(config.communication_step)
        self.output_interval_spin.setValue(
            config.output_interval
            if config.output_interval is not None
            else config.communication_step
        )
        self.use_step_for_output_check.setChecked(config.output_interval is None)
        selected_outputs = set(config.selected_outputs)
        for index in range(self.outputs_list.count()):
            item = self.outputs_list.item(index)
            item.setCheckState(Qt.Checked if item.text() in selected_outputs else Qt.Unchecked)
        self._set_scalar_control_values(self.initial_input_widgets, config.initial_inputs)
        self._set_scalar_control_values(self.parameter_widgets, config.parameters)
        self.input_schedule_editor.setPlainText(
            self._input_schedule_text(config.input_schedule)
        )

        self.current_config = None
        self.run_button.setEnabled(False)
        self.configuration_summary.setText("配置已载入，请先验证后运行。")
        self.statusBar().showMessage("仿真配置已载入。", 5000)
        self._append_operation_log(f"已载入配置：{Path(selected_path).name}。")

    @staticmethod
    def _configuration_file_path(path: Path) -> Path:
        if path.name.endswith(".farcel.json"):
            return path
        return path.with_suffix(".farcel.json")

    def _configuration_matches_loaded_fmu(self, config: SimulationConfig) -> bool:
        """Reject saved variable names absent from the loaded FMU metadata."""
        available_outputs = {
            self.outputs_list.item(index).text()
            for index in range(self.outputs_list.count())
        }
        missing_names = (
            set(config.selected_outputs) - available_outputs
            | set(config.initial_inputs) - set(self.initial_input_widgets)
            | set(config.parameters) - set(self.parameter_widgets)
        )
        unsupported_selected_outputs = {
            self.outputs_list.item(index).text()
            for index in range(self.outputs_list.count())
            if not self.outputs_list.item(index).flags() & Qt.ItemIsEnabled
        } & set(config.selected_outputs)
        if not missing_names and not unsupported_selected_outputs:
            return True

        if unsupported_selected_outputs:
            QMessageBox.warning(
                self,
                "载入配置失败",
                "配置选择了当前不支持采样的变量："
                + ", ".join(sorted(unsupported_selected_outputs)),
            )
            return False

        QMessageBox.warning(
            self,
            "载入配置失败",
            "配置中的变量不属于当前 FMU：" + ", ".join(sorted(missing_names)),
        )
        return False

    @staticmethod
    def _set_scalar_control_values(
        widgets: dict[str, tuple[str, QWidget]], values: object
    ) -> None:
        if not isinstance(values, dict):
            return
        for name, (_, widget) in widgets.items():
            value = values.get(name)
            if isinstance(widget, QComboBox):
                index = widget.findData(value) if name in values else 0
                widget.setCurrentIndex(index if index >= 0 else 0)
            elif isinstance(widget, QPlainTextEdit):
                widget.setPlainText(
                    json.dumps(value, ensure_ascii=False) if name in values else ""
                )
            elif isinstance(widget, QLineEdit):
                widget.setText(str(value) if name in values else "")

    def export_result_to_csv(self) -> None:
        """Export the current public result without re-running the FMU."""
        if self.current_result is None:
            return

        file_stem = self.current_path.stem if self.current_path is not None else "simulation"
        suggested_path = Path.cwd() / "artifacts" / f"{file_stem}-result.csv"
        selected_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 CSV 结果",
            str(suggested_path),
            "CSV 文件 (*.csv)",
        )
        if not selected_path:
            return

        destination = Path(selected_path)
        if destination.suffix.lower() != ".csv":
            destination = destination.with_suffix(".csv")

        try:
            report = self.backend.export_result(self.current_result, destination)
        except EngineError as error:
            message = f"{error.code.value}：{error.message}"
            self.statusBar().showMessage(message, 5000)
            self._append_operation_log(f"CSV 导出失败：{message}")
            QMessageBox.critical(self, "CSV 导出失败", message)
            return

        message = f"CSV 已导出：{report.destination}（{report.row_count} 行）"
        self.configuration_summary.setText(message)
        self.statusBar().showMessage(message, 5000)
        self._append_operation_log(message)
        QMessageBox.information(self, "CSV 导出成功", message)

    def _clear_validation_feedback(self) -> None:
        self.validation_label.clear()
        for widget in (
            self.start_time_spin,
            self.stop_time_spin,
            self.step_size_spin,
            self.output_interval_spin,
            self.use_step_for_output_check,
            self.outputs_list,
            self.variable_tabs,
        ):
            widget.setStyleSheet("")

    def _show_validation_error(self, error: EngineError) -> None:
        issues = error.details.get("issues", ())
        messages = validation_issue_messages(issues)
        if messages:
            self._show_validation_issues(issues)
            return

        self.validation_label.setText(f"{error.code.value}：{error.message}")
        self.configuration_summary.setText("配置验证失败。")
        self.statusBar().showMessage(error.message, 5000)
        self._append_operation_log(f"配置验证失败：{error.message}")

    def _show_validation_issues(self, issues: object) -> None:
        messages = validation_issue_messages(issues)
        self.validation_label.setText("\n".join(messages) or "配置无效。")
        self.configuration_summary.setText("配置验证失败，请修改标红的项目。")
        self.statusBar().showMessage("仿真配置验证失败。", 5000)
        self._append_operation_log("配置验证失败，请检查标红的项目。")

        field_widgets = {
            "start_time": self.start_time_spin,
            "stop_time": self.stop_time_spin,
            "communication_step": self.step_size_spin,
            "output_interval": self.output_interval_spin,
            "selected_outputs": self.outputs_list,
            "initial_inputs": self.variable_tabs,
            "parameters": self.variable_tabs,
            "input_schedule": self.variable_tabs,
        }
        if isinstance(issues, (tuple, list)):
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                widget = field_widgets.get(issue.get("field"))
                if widget is not None:
                    if issue.get("field") == "initial_inputs":
                        self.variable_tabs.setCurrentIndex(1)
                    elif issue.get("field") == "parameters":
                        self.variable_tabs.setCurrentIndex(2)
                    elif issue.get("field") == "input_schedule":
                        self.variable_tabs.setCurrentIndex(3)
                    widget.setStyleSheet("border: 1px solid #B03A2E;")

    def _show_engine_error(self, error: EngineError) -> None:
        self.statusBar().showMessage(error.message, 5000)
        self._append_operation_log(f"FMU 导入失败：{error.code.value}：{error.message}")
        QMessageBox.critical(
            self,
            "无法加载 FMU",
            f"{error.code.value}: {error.message}",
        )

    def _append_operation_log(self, message: str) -> None:
        """Append a timestamped, bounded record of a user-visible operation."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.operation_log.appendPlainText(f"[{timestamp}] {message}")


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
