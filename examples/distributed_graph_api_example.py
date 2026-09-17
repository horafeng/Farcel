"""Run a public mixed or multi-Worker graph with already-running Workers.

The example imports only the supported public backend and contracts surfaces.
Start one or two localhost Workers separately, copy their readiness endpoints,
then pass ``--worker worker-id=host:port`` once or twice.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from farcel import create_backend
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


def parse_worker(value: str) -> WorkerDescriptor:
    worker_id, separator, endpoint = value.partition("=")
    host, colon, port_text = endpoint.rpartition(":")
    if not separator or not worker_id or not colon or not host:
        raise argparse.ArgumentTypeError("worker must be worker-id=host:port")
    try:
        port = int(port_text)
    except ValueError as error:
        raise argparse.ArgumentTypeError("worker port must be an integer") from error
    return WorkerDescriptor(worker_id, WorkerEndpoint(host, port))


def build_graph() -> SimulationGraph:
    return SimulationGraph(
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
        connections=(Connection(PortReference("A", OUTPUT), PortReference("B", INPUT)),),
    )


def build_plan(workers: tuple[WorkerDescriptor, ...]) -> ExecutionPlan:
    if len(workers) not in (1, 2):
        raise ValueError("supply one Worker for mixed execution or two for multi-Worker execution")
    placements = (
        (
            NodePlacement("A", PlacementKind.LOCAL),
            NodePlacement("B", PlacementKind.WORKER, workers[0].worker_id),
        )
        if len(workers) == 1
        else (
            NodePlacement("A", PlacementKind.WORKER, workers[0].worker_id),
            NodePlacement("B", PlacementKind.WORKER, workers[1].worker_id),
        )
    )
    return ExecutionPlan(workers=workers, placements=placements)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="append", type=parse_worker, required=True)
    arguments = parser.parse_args()
    workers = tuple(arguments.worker)
    graph = build_graph()
    config = GraphSimulationConfig(start_time=0.0, stop_time=0.02, communication_step=0.01)
    plan = build_plan(workers)
    backend = create_backend()

    backend.validate_graph(graph, config)
    backend.validate_execution_plan(graph, plan)
    result = backend.run_graph(graph, config, execution_plan=plan)
    print(f"completion state: {result.completion_state.name}")
    print(f"B selected output: {result.node_outputs['B'][OUTPUT]}")


if __name__ == "__main__":
    main()
