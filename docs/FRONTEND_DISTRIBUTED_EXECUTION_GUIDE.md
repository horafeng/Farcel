# Frontend Distributed Execution Guide

This guide is for the Python and PySide6 frontend team. It describes the stable Phase 7 backend boundary; it does not introduce a second frontend scheduler or a new distributed feature.

## 1. Backend architecture overview

```text
GUI
  -> farcel.create_backend()
  -> FarcelEngine public facade
  -> Farcel contracts
  -> local ModelNodeRuntime and/or remote Worker-backed node runtime
```

The GUI owns presentation, user input, its own worker thread, and UI-thread marshaling. The backend owns validation, graph routing, logical time, checkpoint barriers, Worker connection lifecycle for a run, results, and stable errors.

GUI code must never access WorkerRpcClient, TcpWorkerClient, RemoteNodeRuntime, FMPy, sockets, subprocess handles, or any farcel.infrastructure module. Do not construct FarcelEngine with concrete adapters. Use create_backend() and DTOs from farcel.contracts only.

## 2. Public backend API

```python
import farcel
from farcel.contracts import ExecutionPlan

backend = farcel.create_backend()
report = backend.validate_execution_plan(graph, execution_plan)
result = backend.run_graph(graph, config, execution_plan=execution_plan)
```

run_graph is synchronous and execution_plan is keyword-only. graph is a SimulationGraph, config is a GraphSimulationConfig, and execution_plan is optional. Omit the plan for the existing all-local path. validate_execution_plan is pure deployment validation: it does not load an FMU, create a runtime, or contact a Worker.

For a WORKER placement, the descriptor endpoint must identify an already-running localhost Worker. create_backend() itself does not start a Worker or create network I/O.

## 3. Graph model

The frontend creates the system model with Farcel contracts:

- SimulationGraph: ordered ModelNode values plus Connection values.
- ModelNode: node_id, FMU model_path, and ModelNodeConfig.
- Connection: a source PortReference and a target PortReference.

The frontend is responsible for creating nodes and connections, selecting execution interface when needed, and configuring parameters, initial/scheduled inputs, and selected outputs in ModelNodeConfig. Call validate_graph(graph, config) before run_graph.

Graph results use result.timestamps and result.node_outputs[node_id][variable_name]. A routing-only source may be read by the backend without appearing in selected result outputs.

## 4. ExecutionPlan

ExecutionPlan decides where graph nodes run; SimulationGraph continues to describe what is simulated.

```text
LOCAL:  node_A -> local
WORKER: node_B -> worker_001
```

Construct an ExecutionPlan from WorkerDescriptor, WorkerEndpoint, NodePlacement, and PlacementKind. An unplaced node defaults logically to LOCAL. A LOCAL placement must not name a worker. A WORKER placement must reference a declared worker_id.

```python
worker = WorkerDescriptor('worker_001', WorkerEndpoint('127.0.0.1', 51342))
execution_plan = ExecutionPlan(
    workers=(worker,),
    placements=(
        NodePlacement('node_A', PlacementKind.LOCAL),
        NodePlacement('node_B', PlacementKind.WORKER, 'worker_001'),
    ),
)
```

## 5. Distributed execution workflow

```text
user configuration
  -> SimulationGraph
  -> ExecutionPlan
  -> validate_graph + validate_execution_plan
  -> run_graph
  -> RunProgress callbacks and GraphSimulationResult or EngineError
```

The Coordinator preserves explicit-Jacobi previous-checkpoint semantics for local, mixed, and two-Worker graphs. The GUI must not route signals, invent a second time grid, or synchronize Workers directly.

## 6. Progress and result

Pass on_progress when the frontend needs updates. The callback runs on the same thread as run_graph; PySide6 code must emit or queue its own signal to the UI thread.

Present these states:

- RUNNING: use RunProgress current_time, completed_steps, sample_count, and fraction.
- COMPLETED: display GraphSimulationResult final_time, timestamps, and selected output signals.
- STOPPED: display the returned partial GraphSimulationResult; do not label it completed.
- FAILED: catch EngineError and display code, message, and safe details such as phase, node_id, worker_id, and cleanup_failures.

Do not treat Worker wall-clock timing as simulation time. Simulation time and progress are Coordinator-owned committed logical checkpoints.

## 7. Current limitations

Supported: pre-existing localhost Workers, local graphs, mixed LOCAL/WORKER graphs, and multiple explicitly declared localhost Workers.

Not supported:

- Cloud Workers.
- Worker auto-discovery.
- Automatic Worker start, restart, reconnect, retry, or replay.
- Cluster scheduling, Kubernetes, or dynamic Worker pools.
- TLS/authentication or an Internet-facing Worker service.
- GUI access to backend internals or direct Worker-to-Worker orchestration.

See docs/DISTRIBUTED_EXECUTION_USER_GUIDE.md for operational Worker setup and examples/distributed_graph_api_example.py for a command-line public API example.
