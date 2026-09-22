"""Run a Step-to-Gain graph assembled from Farcel standard-block metadata."""

from __future__ import annotations

from dataclasses import replace

from farcel import create_backend
from farcel.application import StandardBlockCatalogLoader, StandardBlockFactory
from farcel.contracts import (
    Connection,
    GraphSimulationConfig,
    PortReference,
    SimulationGraph,
)


STEP_NODE_ID = "step"
GAIN_NODE_ID = "gain"
OUTPUT = "y"
INPUT = "u"


def build_demo_graph() -> SimulationGraph:
    """Build the standard Step-to-Gain graph using the existing contracts."""

    catalog = StandardBlockCatalogLoader().load()
    factory = StandardBlockFactory(catalog)
    step = factory.create_model_node(
        "farcel.sources.step",
        STEP_NODE_ID,
        parameter_overrides={
            "initial_value": 0.0,
            "final_value": 5.0,
            "step_time": 0.01,
        },
    )
    gain = factory.create_model_node(
        "farcel.math.gain",
        GAIN_NODE_ID,
        parameter_overrides={"gain": 3.0},
    )

    # Result recording is graph-specific, so retain Factory-owned config and
    # add the existing ModelNodeConfig field for this demo's observed signal.
    gain = replace(gain, config=replace(gain.config, selected_outputs=(OUTPUT,)))

    return SimulationGraph(
        nodes=(step, gain),
        connections=(
            Connection(
                source=PortReference(STEP_NODE_ID, OUTPUT),
                target=PortReference(GAIN_NODE_ID, INPUT),
            ),
        ),
    )


def main() -> None:
    graph = build_demo_graph()
    config = GraphSimulationConfig(
        start_time=0.0,
        stop_time=0.03,
        communication_step=0.01,
    )
    backend = create_backend()
    report = backend.validate_graph(graph, config)
    result = backend.run_graph(graph, config)

    print(f"graph validation successful: {report.is_valid}")
    print(f"completion state: {result.completion_state.name}")
    print(f"timestamps: {result.timestamps}")
    print(f"Gain.y: {result.node_outputs[GAIN_NODE_ID][OUTPUT]}")
    print("note: explicit-Jacobi routes the previous checkpoint snapshot")


if __name__ == "__main__":
    main()
