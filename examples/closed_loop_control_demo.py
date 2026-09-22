"""Run a standard-block negative-feedback closed-loop control demo."""

from __future__ import annotations

from dataclasses import replace

from farcel import create_backend
from farcel.application import StandardBlockCatalogLoader, StandardBlockFactory
from farcel.contracts import Connection, GraphSimulationConfig, PortReference, SimulationGraph


STEP = "step"
SUM = "sum"
PID = "pid"
PLANT = "first_order"
FEEDBACK = "feedback_gain"
OUTPUT = "y"


def build_closed_loop_graph() -> SimulationGraph:
    """Build Step → Sum → PID → FirstOrder with negative Gain feedback."""

    catalog = StandardBlockCatalogLoader().load()
    factory = StandardBlockFactory(catalog)
    step = factory.create_model_node(
        "farcel.sources.step",
        STEP,
        parameter_overrides={
            "initial_value": 0.0,
            "final_value": 1.0,
            "step_time": 0.0,
        },
    )
    sum_node = factory.create_model_node("farcel.math.sum", SUM)
    pid = factory.create_model_node(
        "farcel.control.pid",
        PID,
        parameter_overrides={"Kp": 2.0, "Ki": 1.0, "Kd": 0.0},
    )
    plant = factory.create_model_node(
        "farcel.continuous.first_order",
        PLANT,
        parameter_overrides={
            "gain": 1.0,
            "time_constant": 1.0,
            "initial_value": 0.0,
        },
    )
    feedback = factory.create_model_node(
        "farcel.math.gain",
        FEEDBACK,
        parameter_overrides={"gain": -1.0},
    )

    plant = replace(plant, config=replace(plant.config, selected_outputs=(OUTPUT,)))

    return SimulationGraph(
        nodes=(step, sum_node, pid, plant, feedback),
        connections=(
            Connection(PortReference(STEP, OUTPUT), PortReference(SUM, "u1")),
            Connection(PortReference(FEEDBACK, OUTPUT), PortReference(SUM, "u2")),
            Connection(PortReference(SUM, OUTPUT), PortReference(PID, "u")),
            Connection(PortReference(PID, OUTPUT), PortReference(PLANT, "u")),
            Connection(PortReference(PLANT, OUTPUT), PortReference(FEEDBACK, "u")),
        ),
    )


def run_demo():
    """Run the demo through the existing public graph backend."""

    graph = build_closed_loop_graph()
    config = GraphSimulationConfig(stop_time=5.0, communication_step=0.05)
    backend = create_backend()
    report = backend.validate_graph(graph, config)
    result = backend.run_graph(graph, config)
    return report, result


def main() -> None:
    report, result = run_demo()
    print(f"图验证成功: {report.is_valid}")
    print(f"完成状态: {result.completion_state.name}")
    print(f"时间轴: {result.timestamps}")
    print(f"一阶环节输出: {result.node_outputs[PLANT][OUTPUT]}")
    final_output = result.node_outputs[PLANT][OUTPUT][-1]
    print("目标值: 1.0")
    print(f"最终输出: {final_output}")
    print(f"最终跟踪误差: {abs(1.0 - final_output)}")
    print("说明: explicit-Jacobi 使用上一个 checkpoint 的 source snapshot")


if __name__ == "__main__":
    main()
