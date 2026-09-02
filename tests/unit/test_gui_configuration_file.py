from pathlib import Path

from farcel.contracts import SimulationConfig
from gui.configuration_file import configuration_payload, read_configuration_payload


def test_configuration_payload_round_trips_single_fmu_settings() -> None:
    path = Path("example.fmu")
    config = SimulationConfig(
        start_time=1.0,
        stop_time=2.0,
        communication_step=0.05,
        output_interval=0.1,
        selected_outputs=("speed",),
        parameters={"gain": 2.0},
        initial_inputs={"enabled": True},
    )

    restored_path, restored_config = read_configuration_payload(
        configuration_payload(path, config)
    )

    assert restored_path == path
    assert restored_config.start_time == 1.0
    assert restored_config.stop_time == 2.0
    assert restored_config.communication_step == 0.05
    assert restored_config.output_interval == 0.1
    assert restored_config.selected_outputs == ("speed",)
    assert restored_config.parameters == {"gain": 2.0}
    assert restored_config.initial_inputs == {"enabled": True}


def test_configuration_payload_accepts_legacy_missing_output_interval() -> None:
    _, restored_config = read_configuration_payload(
        {
            "format_version": 1,
            "fmu_path": "example.fmu",
            "simulation": {
                "start_time": 0.0,
                "stop_time": 1.0,
                "communication_step": 0.01,
                "selected_outputs": [],
                "parameters": {},
                "initial_inputs": {},
            },
        }
    )

    assert restored_config.output_interval is None


def test_configuration_payload_rejects_invalid_payload() -> None:
    try:
        read_configuration_payload({"format_version": 99})
    except ValueError as error:
        assert "不支持" in str(error)
    else:
        raise AssertionError("invalid configuration payload should fail")
