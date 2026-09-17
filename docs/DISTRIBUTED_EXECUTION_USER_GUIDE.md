# Distributed Execution User Guide

## Scope

Farcel supports distributed logical-time execution through the ordinary public backend surface:

```python
from farcel import create_backend
from farcel.contracts import ExecutionPlan

backend = create_backend()
result = backend.run_graph(graph, config, execution_plan=plan)
```

`execution_plan` is keyword-only. Public callers import deployment DTOs from `farcel.contracts`; they must not import `WorkerRpcClient`, `TcpWorkerClient`, `RemoteNodeRuntime`, FMPy, or a Worker implementation class.

The Coordinator continues to own graph routing, global logical time, checkpoint barriers, results, progress, and stop control. A Worker runs only the node runtime lifecycle requested by the Coordinator.

## Local simulation

No `ExecutionPlan` is required for an all-local graph:

```python
backend = create_backend()
backend.validate_graph(graph, config)
result = backend.run_graph(graph, config)
```

The existing [graph public API example](../examples/graph_api_example.py) is a complete two-node local example. Supplying no plan, an empty plan, explicit LOCAL placements, or unused Worker descriptors keeps this exact local execution path.

## Start a localhost Worker

Start the Worker separately from the application that calls `run_graph`:

```powershell
python -m farcel.worker --worker-id worker-a --cache-root C:\farcel-worker-a --port 0
```

The Worker prints a readiness JSON document containing its `worker_id`, loopback host, and allocated port. Keep the process running and use those values to build its `WorkerDescriptor`. `create_backend()` neither starts a Worker nor performs network I/O until a plan actually places a node on one.

The supported automated environment is localhost. A Worker endpoint is a pre-existing trusted endpoint; there is no automatic spawn, restart, discovery, reconnect, retry, or replay.

## Single-Worker simulation

For one Worker, place one or more graph nodes on its descriptor:

```python
from farcel.contracts import (
    ExecutionPlan, NodePlacement, PlacementKind, WorkerDescriptor, WorkerEndpoint,
)

worker = WorkerDescriptor("worker-a", WorkerEndpoint("127.0.0.1", 51342))
plan = ExecutionPlan(
    workers=(worker,),
    placements=(NodePlacement("B", PlacementKind.WORKER, "worker-a"),),
)
backend.validate_execution_plan(graph, plan)
result = backend.run_graph(graph, config, execution_plan=plan)
```

The node's FMU is content-addressed and staged into the Worker-owned cache. The Coordinator path is never sent to the Worker.

## Mixed LOCAL/WORKER simulation

The single-Worker plan above is also a mixed graph when unplaced nodes remain local. An explicit LOCAL placement is optional but can make the deployment intent clear:

```python
plan = ExecutionPlan(
    workers=(worker,),
    placements=(
        NodePlacement("A", PlacementKind.LOCAL),
        NodePlacement("B", PlacementKind.WORKER, "worker-a"),
    ),
)
```

The same explicit-Jacobi semantics apply across the boundary: routing uses the previous immutable checkpoint snapshot, and a feedback edge has exactly one checkpoint of delay.

## Multi-Worker simulation

Place nodes on separate pre-started descriptors to use more than one Worker:

```python
worker_a = WorkerDescriptor("worker-a", WorkerEndpoint("127.0.0.1", 51342))
worker_b = WorkerDescriptor("worker-b", WorkerEndpoint("127.0.0.1", 51343))
plan = ExecutionPlan(
    workers=(worker_a, worker_b),
    placements=(
        NodePlacement("A", PlacementKind.WORKER, "worker-a"),
        NodePlacement("B", PlacementKind.WORKER, "worker-b"),
    ),
)
result = backend.run_graph(graph, config, execution_plan=plan)
```

Farcel opens one session for each *used* Worker during the run and closes those sessions after node-runtime cleanup. Workers do not connect to each other; all coupling remains at the Coordinator.

The runnable [distributed graph example](../examples/distributed_graph_api_example.py) accepts one `--worker worker-id=host:port` argument for a mixed graph or two arguments for a two-Worker graph. It imports only `farcel` and `farcel.contracts`.

## Failure behavior

Execution-plan validation is local and happens before Worker I/O or native runtime creation. An invalid plan raises Farcel's stable validation error with structured issues.

After a placed run begins, connect/handshake/operation failures, malformed protocol responses, Worker loss, and Worker crashes fail the graph. Stateful Worker commands are not retried or replayed. If a failure occurs before the all-output-read and checkpoint-commit boundary, Farcel does not return or publish a successful partial checkpoint. It best-effort terminates/closes surviving node runtimes, then closes the TCP sessions it owns. Cleanup failures are reported as `cleanup_failures` without replacing a primary run error.

`RunControl` remains Coordinator-local. A stop requested during an in-flight advance is observed at the ordinary committed-checkpoint boundary; it does not force-kill a Worker or native FMU call.

## Project behavior

`SimulationProject` schema `1.0` deliberately does **not** persist `ExecutionPlan`, endpoint, process, or credential information. `SimulationCase` remains portable and `run_project_case` remains local-only in this release. Use the direct public graph API above when selecting Workers at runtime.

Endpoint-bearing plans are deployment configuration, not project-model data. A future project feature must persist only logical placement references and resolve host/port from a runtime-supplied deployment registry; it must not place machine-specific endpoints in `project.json`.

## Current limitations

- Localhost Worker execution is the supported and tested deployment mode.
- There is no Worker pool, automatic Worker lifecycle, dynamic cluster, scheduler, Kubernetes, cloud deployment, or checkpoint recovery.
- No TLS, authentication, authorization, Internet-facing service, or credential persistence is provided.
- No automatic reconnect, retry, replay, Worker restart, or rollback is performed.
- Graph numerical semantics remain explicit-Jacobi; strong coupling is not implemented.
