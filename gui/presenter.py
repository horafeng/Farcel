"""Display-oriented transformations for Farcel public contracts."""

from __future__ import annotations

from farcel.contracts import ModelMetadata, SimulationResult, VariableMetadata


def runtime_channel_warning(variable: VariableMetadata) -> str | None:
    """Describe public variable types the current backend cannot use at runtime."""
    data_type = variable.data_type.casefold()
    if data_type == "binary":
        return "Binary 变量当前不能作为运行时输入或输出。"
    if data_type == "clock":
        return "Clock 变量当前不能作为运行时输入或输出。"
    return None


def model_summary(metadata: ModelMetadata) -> tuple[tuple[str, str], ...]:
    """Return stable model details for the GUI property panel."""
    interfaces = ", ".join(interface.value for interface in metadata.interface_types)
    executable = (
        metadata.executable_interface.value
        if metadata.executable_interface is not None
        else "当前版本不可执行"
    )
    advanced_capabilities = []
    if metadata.capabilities.supports_event_mode:
        advanced_capabilities.append("Event Mode")
    if metadata.capabilities.supports_early_return:
        advanced_capabilities.append("Early Return")
    details = [
        ("模型名称", metadata.model_name),
        ("FMI 版本", metadata.fmi_version),
        ("接口类型", interfaces or "未声明"),
        ("可执行接口", executable),
        ("变量数量", str(len(metadata.variables))),
    ]
    if advanced_capabilities:
        details.append(("FMI 3 运行能力", ", ".join(advanced_capabilities)))
    return tuple(details)


def variable_rows(metadata: ModelMetadata) -> tuple[tuple[str, str, str, str, str, str, str], ...]:
    """Return table rows containing the public details useful for configuration."""
    return tuple(variable_table_row(variable) for variable in metadata.variables)


def variable_table_row(variable: VariableMetadata) -> tuple[str, str, str, str, str, str, str]:
    """Convert one public variable to a readable table row."""
    return (
        variable.name,
        variable.data_type,
        variable.causality or "-",
        _display_value(variable.start),
        _display_value(variable.minimum),
        _display_value(variable.maximum),
        variable.unit or "-",
    )


def filtered_variables(
    metadata: ModelMetadata, search_text: str, causality_filter: str
) -> tuple[VariableMetadata, ...]:
    """Return public variables matching a name search and GUI causality category."""
    query = search_text.casefold().strip()
    return tuple(
        variable
        for variable in metadata.variables
        if (not query or query in variable.name.casefold())
        and _matches_causality_filter(variable, causality_filter)
    )


def variable_detail_text(variable: VariableMetadata) -> str:
    """Return a compact, readable description for a selected public variable."""
    return "\n".join(
        (
            f"名称：{variable.name}",
            f"类型：{variable.data_type}",
            f"方向：{variable.causality or '-'}",
            f"初始值：{_display_value(variable.start)}",
            f"范围：{_display_value(variable.minimum)} ～ {_display_value(variable.maximum)}",
            f"单位：{variable.unit or '-'}",
            f"说明：{variable.description or '无'}",
        )
    )


def _matches_causality_filter(variable: VariableMetadata, causality_filter: str) -> bool:
    if causality_filter == "all":
        return True
    if causality_filter == "parameter":
        return variable.causality in {"parameter", "structuralParameter"}
    if causality_filter == "other":
        return variable.causality not in {"input", "output", "parameter", "structuralParameter"}
    return variable.causality == causality_filter


def _display_value(value: object) -> str:
    return "-" if value is None else str(value)


def simulation_defaults(metadata: ModelMetadata) -> tuple[float, float, float]:
    """Return safe GUI defaults from the FMU's public DefaultExperiment."""
    experiment = metadata.default_experiment
    start_time = experiment.start_time if experiment.start_time is not None else 0.0
    stop_time = experiment.stop_time if experiment.stop_time is not None else start_time + 1.0
    if stop_time <= start_time:
        stop_time = start_time + 1.0

    communication_step = experiment.step_size or 0.01
    if communication_step <= 0:
        communication_step = 0.01
    return start_time, stop_time, communication_step


def output_variable_names(metadata: ModelMetadata) -> tuple[str, ...]:
    """Return the public output variables selectable by a single-FMU run."""
    return tuple(
        variable.name
        for variable in metadata.variables
        if variable.causality == "output"
    )


def input_variables(metadata: ModelMetadata) -> tuple[VariableMetadata, ...]:
    """Return public FMU input variables for the initial-inputs page."""
    return tuple(
        variable
        for variable in metadata.variables
        if variable.causality == "input"
    )


def parameter_variables(metadata: ModelMetadata) -> tuple[VariableMetadata, ...]:
    """Return public FMU parameters configurable before initialization."""
    return tuple(
        variable
        for variable in metadata.variables
        if variable.causality in {"parameter", "structuralParameter"}
    )


def validation_issue_messages(issues: object) -> tuple[str, ...]:
    """Convert the documented CONFIG_ERROR issue schema into GUI text."""
    if not isinstance(issues, (tuple, list)):
        return ()

    messages = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        field = issue.get("field", "配置")
        message = issue.get("message", "配置无效")
        messages.append(f"{field}：{message}")
    return tuple(messages)


def result_summary(result: SimulationResult) -> tuple[tuple[str, str], ...]:
    """Return a compact display summary for a completed public result."""
    selected_outputs = ", ".join(result.outputs) or "未选择输出变量"
    return (
        ("运行状态", "完成" if result.successful else result.completion_state.value),
        ("完成步数", str(result.completed_steps)),
        ("采样数量", str(result.sample_count)),
        ("最终时间", str(result.final_time)),
        ("结果变量", selected_outputs),
    )


def result_table_data(
    result: SimulationResult,
) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    """Convert public result samples into display-ready table headers and rows."""
    output_names = tuple(result.outputs)
    headers = ("时间", *output_names)
    rows = tuple(
        (
            str(timestamp),
            *(str(result.outputs[name][sample_index]) for name in output_names),
        )
        for sample_index, timestamp in enumerate(result.timestamps)
    )
    return headers, rows


def result_statistics(
    result: SimulationResult,
) -> tuple[tuple[str, str, str, str, str], ...]:
    """Return simple numerical statistics for every selected public output."""
    rows = []
    for name, values in result.outputs.items():
        try:
            numeric_values = tuple(float(value) for value in values)
            if any(isinstance(value, bool) for value in values):
                raise ValueError
        except (TypeError, ValueError):
            final_value = _display_value(values[-1]) if values else "-"
            rows.append((name, "不适用", "不适用", "不适用", final_value))
            continue

        if not numeric_values:
            rows.append((name, "-", "-", "-", "-"))
            continue

        rows.append(
            (
                name,
                _format_statistic(min(numeric_values)),
                _format_statistic(max(numeric_values)),
                _format_statistic(sum(numeric_values) / len(numeric_values)),
                _format_statistic(numeric_values[-1]),
            )
        )
    return tuple(rows)


def _format_statistic(value: float) -> str:
    """Keep result statistics compact without hiding useful precision."""
    return f"{value:.8g}"


def result_plot_series(
    result: SimulationResult,
) -> tuple[tuple[str, tuple[float, ...], tuple[object, ...]], ...]:
    """Pair each selected public output with the runtime-provided time axis."""
    timestamps = tuple(result.timestamps)
    return tuple(
        (name, timestamps, tuple(values))
        for name, values in result.outputs.items()
    )


def scalar_plot_series(
    result: SimulationResult,
) -> tuple[tuple[str, tuple[float, ...], tuple[object, ...]], ...]:
    """Expand public array outputs into named scalar series for plotting only."""
    expanded_series = []
    for name, timestamps, values in result_plot_series(result):
        if not values or not isinstance(values[0], tuple):
            expanded_series.append((name, timestamps, values))
            continue

        first_sample = dict(_flatten_array_sample(values[0]))
        if not first_sample:
            continue
        samples = tuple(dict(_flatten_array_sample(value)) for value in values)
        paths = tuple(first_sample)
        if any(tuple(sample) != paths for sample in samples):
            continue
        for path in paths:
            suffix = ",".join(str(index) for index in path)
            expanded_series.append(
                (f"{name}[{suffix}]", timestamps, tuple(sample[path] for sample in samples))
            )
    return tuple(expanded_series)


def _flatten_array_sample(
    value: object, path: tuple[int, ...] = ()
) -> tuple[tuple[tuple[int, ...], object], ...]:
    """Flatten a nested public array value while retaining its zero-based indices."""
    if not isinstance(value, tuple):
        return ((path, value),)
    flattened = []
    for index, item in enumerate(value):
        flattened.extend(_flatten_array_sample(item, (*path, index)))
    return tuple(flattened)
