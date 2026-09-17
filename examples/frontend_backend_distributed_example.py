"""Minimal PySide6-facing reference for a mixed distributed graph.

The caller starts worker_001 separately, then provides its localhost endpoint.
Only the public farcel and farcel.contracts surfaces are used.
"""

from pathlib import Path

import farcel
from farcel.contracts import (
    Connection,
    ExecutionPlan,
    GraphSimulationConfig,
    InterfaceType,
    ModelNode,
    ModelNodeConfig,
    NodePlacement,
    PlacementKind,
    PortReference,
    SimulationGraph,
    WorkerDescriptor,
    WorkerEndpoint,
)


ROOT = Path(__file__).resolve().parents[1]
FMU = ROOT / "examples" / "fmus" / "Feedthrough-fmi2.fmu"
INPUT = "Float64_continuous_input"
OUTPUT = "Float64_continuous_output"


def build_graph() -> SimulationGraph:
    return SimulationGraph(
        nodes=(
            ModelNode(
                "node_A",
                str(FMU),
                ModelNodeConfig(
                    initial_inputs={INPUT: 2.0},
                    execution_interface=InterfaceType.CO_SIMULATION,
                ),
            ),
            ModelNode(
                "node_B",
                str(FMU),
                ModelNodeConfig(
                    selected_outputs=(OUTPUT,),
                    execution_interface=InterfaceType.CO_SIMULATION,
                ),
            ),
        ),
        connections=(
            Connection(PortReference("node_A", OUTPUT), PortReference("node_B", INPUT)),
        ),
    )


def build_execution_plan() -> ExecutionPlan:
    worker = WorkerDescriptor("worker_001", WorkerEndpoint("127.0.0.1", 51342))
    return ExecutionPlan(
        workers=(worker,),
        placements=(
            NodePlacement("node_A", PlacementKind.LOCAL),
            NodePlacement("node_B", PlacementKind.WORKER, "worker_001"),
        ),
    )


def main() -> None:
    graph = build_graph()
    config = GraphSimulationConfig(start_time=0.0, stop_time=0.02, communication_step=0.01)
    execution_plan = build_execution_plan()
    backend = farcel.create_backend()

    backend.validate_graph(graph, config)
    backend.validate_execution_plan(graph, execution_plan)
    result = backend.run_graph(graph, config, execution_plan=execution_plan)

    print(f"state: {result.completion_state.name}")
    print(f"time: {result.final_time}")
    print(f"node_B output: {result.node_outputs['node_B'][OUTPUT]}")


if __name__ == "__main__":
    main()
