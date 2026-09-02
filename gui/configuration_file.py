"""Portable JSON representation for one Farcel GUI run configuration."""

from __future__ import annotations

from pathlib import Path

from farcel.contracts import InputUpdate, SimulationConfig


FORMAT_VERSION = 1


def configuration_payload(fmu_path: Path, config: SimulationConfig) -> dict[str, object]:
    """Return a JSON-ready single-FMU configuration using only public contracts."""
    return {
        "format_version": FORMAT_VERSION,
        "fmu_path": str(fmu_path),
        "simulation": {
            "start_time": config.start_time,
            "stop_time": config.stop_time,
            "communication_step": config.communication_step,
            "output_interval": config.output_interval,
            "selected_outputs": list(config.selected_outputs),
            "parameters": dict(config.parameters),
            "initial_inputs": dict(config.initial_inputs),
            "input_schedule": [
                {"time": update.time, "values": dict(update.values)}
                for update in config.input_schedule
            ],
        },
    }


def read_configuration_payload(payload: object) -> tuple[Path, SimulationConfig]:
    """Validate and decode the GUI-owned JSON configuration shape."""
    if not isinstance(payload, dict) or payload.get("format_version") != FORMAT_VERSION:
        raise ValueError("不是受支持的 Farcel 配置文件。")

    fmu_path = payload.get("fmu_path")
    simulation = payload.get("simulation")
    if not isinstance(fmu_path, str) or not fmu_path:
        raise ValueError("配置文件缺少 FMU 路径。")
    if not isinstance(simulation, dict):
        raise ValueError("配置文件缺少仿真配置。")

    selected_outputs = simulation.get("selected_outputs", ())
    parameters = simulation.get("parameters", {})
    initial_inputs = simulation.get("initial_inputs", {})
    output_interval = simulation.get("output_interval")
    input_schedule = simulation.get("input_schedule", [])
    if (
        not isinstance(selected_outputs, list)
        or not all(isinstance(name, str) for name in selected_outputs)
        or not isinstance(parameters, dict)
        or not isinstance(initial_inputs, dict)
        or not isinstance(input_schedule, list)
    ):
        raise ValueError("配置文件中的变量配置格式无效。")

    parameters = {
        name: _json_array_to_tuple(value) for name, value in parameters.items()
    }
    initial_inputs = {
        name: _json_array_to_tuple(value) for name, value in initial_inputs.items()
    }
    updates = _read_input_schedule(input_schedule)

    try:
        config = SimulationConfig(
            start_time=float(simulation["start_time"]),
            stop_time=float(simulation["stop_time"]),
            communication_step=float(simulation["communication_step"]),
            output_interval=(
                None if output_interval is None else float(output_interval)
            ),
            selected_outputs=tuple(selected_outputs),
            parameters=parameters,
            initial_inputs=initial_inputs,
            input_schedule=updates,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("配置文件中的时间或步长无效。") from error
    return Path(fmu_path), config


def _json_array_to_tuple(value: object) -> object:
    """Restore JSON arrays to Farcel's nested tuple public representation."""
    if isinstance(value, list):
        return tuple(_json_array_to_tuple(item) for item in value)
    return value


def _read_input_schedule(payload: list[object]) -> tuple[InputUpdate, ...]:
    """Decode the GUI JSON schedule shape to public InputUpdate objects."""
    updates = []
    for entry in payload:
        if not isinstance(entry, dict):
            raise ValueError("配置文件中的时变输入格式无效。")
        time = entry.get("time")
        values = entry.get("values")
        if (
            isinstance(time, bool)
            or not isinstance(time, (int, float))
            or not isinstance(values, dict)
            or not all(isinstance(name, str) for name in values)
        ):
            raise ValueError("配置文件中的时变输入格式无效。")
        updates.append(
            InputUpdate(
                float(time),
                {name: _json_array_to_tuple(value) for name, value in values.items()},
            )
        )
    return tuple(updates)
