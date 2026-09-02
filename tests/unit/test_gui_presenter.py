from farcel.contracts import (
    DefaultExperiment,
    InterfaceType,
    ModelMetadata,
    SimulationResult,
    SimulationState,
    VariableMetadata,
)
from gui.presenter import (
    model_summary,
    input_variables,
    filtered_variables,
    output_variable_names,
    parameter_variables,
    result_plot_series,
    result_statistics,
    result_table_data,
    result_summary,
    runtime_channel_warning,
    simulation_defaults,
    variable_detail_text,
    validation_issue_messages,
    variable_rows,
)


def test_presenter_uses_only_displayable_contract_fields() -> None:
    metadata = ModelMetadata(
        model_id="model-1",
        source_path="example.fmu",
        fmi_version="2.0",
        model_name="Example Model",
        interface_types=(InterfaceType.CO_SIMULATION,),
        executable_interface=InterfaceType.CO_SIMULATION,
        variables=(
            VariableMetadata(
                name="speed",
                value_reference=42,
                data_type="Real",
                causality="output",
                unit="m/s",
            ),
        ),
    )

    assert ("模型名称", "Example Model") in model_summary(metadata)
    assert variable_rows(metadata) == (("speed", "Real", "output", "-", "-", "-", "m/s"),)


def test_presenter_derives_run_defaults_and_selectable_outputs() -> None:
    metadata = ModelMetadata(
        model_id="model-2",
        source_path="example.fmu",
        fmi_version="3.0",
        model_name="Example Model",
        interface_types=(InterfaceType.CO_SIMULATION,),
        default_experiment=DefaultExperiment(start_time=2.0, stop_time=4.0, step_size=0.1),
        variables=(
            VariableMetadata("speed", 1, "Float64", causality="output"),
            VariableMetadata("target", 2, "Float64", causality="input"),
            VariableMetadata("enabled", 3, "Boolean", causality="output"),
            VariableMetadata("gain", 4, "Float64", causality="parameter"),
        ),
    )

    assert simulation_defaults(metadata) == (2.0, 4.0, 0.1)
    assert output_variable_names(metadata) == ("speed", "enabled")
    assert tuple(variable.name for variable in input_variables(metadata)) == ("target",)
    assert tuple(variable.name for variable in parameter_variables(metadata)) == ("gain",)
    assert tuple(variable.name for variable in filtered_variables(metadata, "spe", "output")) == ("speed",)
    assert tuple(variable.name for variable in filtered_variables(metadata, "", "parameter")) == ("gain",)
    assert "说明：无" in variable_detail_text(metadata.variables[0])


def test_presenter_marks_binary_and_clock_runtime_channels_as_unsupported() -> None:
    binary_output = VariableMetadata("payload", 1, "Binary", causality="output")
    clock_input = VariableMetadata("tick", 2, "Clock", causality="input")

    assert runtime_channel_warning(binary_output) is not None
    assert runtime_channel_warning(clock_input) is not None


def test_presenter_formats_only_documented_validation_issue_fields() -> None:
    issues = (
        {"field": "stop_time", "code": "INVALID_TIME_RANGE", "message": "结束时间必须更大。"},
        {"field": "communication_step", "code": "INVALID_STEP_SIZE", "message": "步长必须为正数。"},
    )

    assert validation_issue_messages(issues) == (
        "stop_time：结束时间必须更大。",
        "communication_step：步长必须为正数。",
    )


def test_presenter_summarizes_public_simulation_result() -> None:
    result = SimulationResult(
        fmu_path="example.fmu",
        start_time=0.0,
        stop_time=0.02,
        step_size=0.01,
        completed_steps=2,
        final_time=0.02,
        completion_state=SimulationState.COMPLETED,
        timestamps=(0.0, 0.01, 0.02),
        outputs={"x0": (0.0, 0.5, 1.0)},
    )

    assert ("运行状态", "完成") in result_summary(result)
    assert ("结果变量", "x0") in result_summary(result)


def test_presenter_converts_public_result_samples_to_table_rows() -> None:
    result = SimulationResult(
        fmu_path="example.fmu",
        start_time=0.0,
        stop_time=0.02,
        step_size=0.01,
        completed_steps=2,
        final_time=0.02,
        completion_state=SimulationState.COMPLETED,
        timestamps=(0.0, 0.01, 0.02),
        outputs={"x0": (0.0, 0.5, 1.0), "x1": (1.0, 0.8, 0.2)},
    )

    headers, rows = result_table_data(result)

    assert headers == ("时间", "x0", "x1")
    assert rows == (("0.0", "0.0", "1.0"), ("0.01", "0.5", "0.8"), ("0.02", "1.0", "0.2"))


def test_presenter_calculates_output_statistics_from_public_samples() -> None:
    result = SimulationResult(
        fmu_path="example.fmu",
        start_time=0.0,
        stop_time=0.02,
        step_size=0.01,
        completed_steps=2,
        final_time=0.02,
        completion_state=SimulationState.COMPLETED,
        timestamps=(0.0, 0.01, 0.02),
        outputs={"x0": (0.0, 0.5, 1.0), "mode": ("idle", "run", "stop")},
    )

    assert result_statistics(result) == (
        ("x0", "0", "1", "0.5", "1"),
        ("mode", "不适用", "不适用", "不适用", "stop"),
    )


def test_presenter_pairs_plot_series_with_public_timestamps() -> None:
    result = SimulationResult(
        fmu_path="example.fmu",
        start_time=0.0,
        stop_time=0.01,
        step_size=0.01,
        completed_steps=1,
        final_time=0.01,
        completion_state=SimulationState.COMPLETED,
        timestamps=(0.0, 0.01),
        outputs={"x0": (2.0, 2.0)},
    )

    assert result_plot_series(result) == (
        ("x0", (0.0, 0.01), (2.0, 2.0)),
    )
