# Phase 8.5.2 — Standard Block Graph Integration Demo

## Demo flow

[`examples/standard_block_graph_demo.py`](../examples/standard_block_graph_demo.py)
demonstrates that standard-block metadata enters the existing graph API without
adding a new node type or runtime:

```text
StandardBlockCatalog
    ↓
StandardBlockFactory
    ↓
Step ModelNode + Gain ModelNode
    ↓
SimulationGraph (Step.y → Gain.u)
    ↓
run_graph
```

The factory resolves each registered `BlockDescriptor` to the existing
`ModelNode` contract. The graph owner adds the usual graph-specific recording
choice, `selected_outputs=("y",)`, to the Gain node configuration. Parameters,
the packaged FMU asset path, and execution interface remain Factory-owned
descriptor data.

## Jacobi-aware result

The demo uses a Step block with `initial_value=0`, `final_value=5`, and
`step_time=0.01`, followed by a Gain block with `gain=3`. With a stop time of
`0.03` and a communication step of `0.01`, the result is:

```text
timestamps: (0.0, 0.01, 0.02, 0.03)
Gain.y:    (0.0, 0.0, 15.0, 15.0)
```

`SimulationGraph` uses its existing explicit-Jacobi checkpoint scheduler.
Connections route the source snapshot from the previous checkpoint: Step first
reports `5` at the `0.01` checkpoint, and Gain receives that value for its
advance to `0.02`. The one-checkpoint propagation delay is therefore expected
runtime semantics, not a Standard Block defect.

## Boundary preserved

This phase changes neither `SimulationGraph` nor its routing logic, solver,
FMU semantics, Worker protocol, project schema, or GUI. The demo invokes the
existing public backend `validate_graph()` and `run_graph()` APIs, so existing
runtime ownership remains unchanged.
