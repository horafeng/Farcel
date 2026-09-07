"""Run a two-node public SimulationGraph without using application internals."""

from pathlib import Path

from farcel import create_backend
from farcel.contracts import (
    Connection,
    GraphSimulationConfig,
    InterfaceType,
    ModelNode,
    ModelNodeConfig,
    PortReference,
    SimulationGraph,
)


ROOT = Path(__file__).resolve().parents[1]
FMU = ROOT / "examples" / "fmus" / "Feedthrough-fmi2.fmu"
INPUT = "Float64_continuous_input"
OUTPUT = "Float64_continuous_output"


def main() -> None:
    graph = SimulationGraph(
        nodes=(
            ModelNode(
                "A",
                str(FMU),
                ModelNodeConfig(
                    initial_inputs={INPUT: 2.0},
                    execution_interface=InterfaceType.CO_SIMULATION,
                ),
            ),
            ModelNode(
                "B",
                str(FMU),
                ModelNodeConfig(
                    selected_outputs=(OUTPUT,),
                    execution_interface=InterfaceType.CO_SIMULATION,
                ),
            ),
        ),
        connections=(
            Connection(PortReference("A", OUTPUT), PortReference("B", INPUT)),
        ),
    )
    config = GraphSimulationConfig(
        start_time=0.0, stop_time=.02, communication_step=.01
    )
    backend = create_backend()
    report = backend.validate_graph(graph, config)
    result = backend.run_graph(graph, config)

    print(f"graph validation successful: {report.is_valid}")
    print(f"completion state: {result.completion_state.name}")
    print(f"completed steps: {result.completed_steps}")
    print(f"samples: {result.sample_count}")
    print(f"final time: {result.final_time}")
    print(f"B selected output: {result.node_outputs['B'][OUTPUT]}")
    print(f"A routing-only result empty: {result.node_outputs['A'] == {}}")


if __name__ == "__main__":
    main()
