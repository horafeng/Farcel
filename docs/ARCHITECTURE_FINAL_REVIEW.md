# Farcel Final Architecture Review

## Release baseline

This document records the Phase 7.6 release-readiness audit originally made on
`phase-7-work` after Phase 7.5B. Phase 7 was subsequently merged into `main`,
which now includes the delivered localhost distributed-graph architecture. The
review itself is documentation only; no runtime, protocol, scheduling, routing,
or public API behavior is changed.

## Overall architecture

```text
GUI / CLI / public Python consumer
              |
              v
     farcel.create_backend()
              |
              v
       FarcelEngine application facade
              |
              v
contracts: DTOs, errors, ports, protocols
              ^
              |
infrastructure: FMPy, CSV, project JSON, Worker TCP/process adapters
```

The default composition root is src/farcel/backend.py. It supplies FMPy adapters, CSV exporters, project repositories, and the existing TCP distributed executor to FarcelEngine. Consumers use create_backend() and do not construct infrastructure adapters.

## Dependency audit

| Rule | Result |
| --- | --- |
| contracts imports neither application nor infrastructure | Pass |
| application imports no infrastructure module | Pass |
| infrastructure imports no application module | Pass |

The fixed direction is GUI / CLI -> application -> contracts <- infrastructure. Composition modules sit outside those layers solely to assemble contract implementations.

## Module responsibilities

| Area | Responsibility |
| --- | --- |
| contracts | Stable DTOs, enums, errors, graph/project/distributed declarations, ports, and SimulationEngine. |
| application | Validation, engine facade, FMU/graph/project use cases, graph scheduling/routing, remote-node proxy, and Worker RPC semantics via contracts-owned ports. |
| infrastructure/fmpy | The only FMPy boundary; owns native lifecycle and maps it to Farcel values/errors. |
| infrastructure/export | Writes an existing canonical result to CSV; it never reruns a simulation. |
| infrastructure/project | Persists project.json and result artifacts; it never validates or executes a graph. |
| infrastructure/worker_protocol and worker_process | TCP framing/codec/transport and optional localhost Worker bootstrap. |
| backend.py and distributed_backend.py | Public composition, including per-run connection composition for pre-existing Worker endpoints. |
| cli.py | Thin presentation and argument parsing for public backend capabilities. |

## Data flow

### Single FMU

```text
caller -> FarcelEngine.run_fmu()
       -> metadata/config validation
       -> Farcel session/runner contract
       -> infrastructure.fmpy adapter
       -> canonical SimulationResult -> optional CSV exporter
```

Parsing and executability stay separate. Unsupported runtime interfaces return stable Farcel errors rather than leaking adapter objects.

### Local graph

```text
caller -> validate_graph() / run_graph()
       -> GraphValidator -> GraphRuntimeBindingsFactory
       -> GraphSimulationRunner
       -> DataRouter + SimulationOrchestrator
       -> GraphSimulationResult
```

The Coordinator routes from the previous immutable snapshot, sets all inputs, advances all nodes, reads all outputs, and then commits. SimulationOrchestrator, DataRouter, and GraphSimulationRunner are the only owners of these graph semantics.

### Distributed graph

```text
caller -> run_graph(..., execution_plan=plan)
       -> pure ExecutionPlan validation
       -> TcpDistributedGraphExecutor for used WORKER placements
       -> GraphRuntimeBindingsFactory.create_with_plan()
       -> local ModelNodeRuntime and/or RemoteNodeRuntime bindings
       -> unchanged GraphSimulationRunner / SimulationOrchestrator
       -> WorkerRpcClient -> TCP transport -> pre-existing Worker process
```

ExecutionPlan is separate from SimulationGraph. The Coordinator owns topology, routing, logical time, progress, results, and checkpoint commit; a Worker owns only its cache and node lifecycle. The executor opens one connection per used Worker and closes it after node-runtime cleanup. It does not spawn, restart, retry, reconnect, or replay a Worker.

Mixed LOCAL/WORKER, two-Worker coupling, feedback delay, completion-order independence, and crash-before-commit preserve the same graph semantics.

### Project

```text
project.json -> SimulationProject / SimulationCase
             -> project validation and path resolution
             -> existing graph validation/run path
             -> ProjectRunArtifact + run history
```

Project schema 1.0 is deployment-neutral: it stores no Worker endpoint, process, credential, or ExecutionPlan. Project cases remain portable and local-only; runtime-selected distribution uses the direct public graph API.

## Public API review

create_backend() is sufficient for local and distributed graph execution. SimulationEngine declares graph validation, independent plan validation, and the additive keyword-only run_graph(..., execution_plan=None) signature. Omitting a plan preserves the local path.

Public callers use only farcel.create_backend, the returned engine facade, and DTOs/errors from farcel.contracts. GUI follows exactly that boundary. WorkerRpcClient, TcpWorkerClient, RemoteNodeRuntime, FMPy objects, sockets, subprocess handles, and native handles are not public API. No new public API is needed.

## CLI review

| Command | Release role |
| --- | --- |
| farcel inspect FMU [--json] | Show normalized metadata and parse/execution capability. |
| farcel validate FMU ... | Demonstrate public configuration validation. |
| farcel run FMU ... | Execute a supported single-FMU workflow and show a canonical result summary. |
| farcel export FMU --csv PATH ... | Execute the workflow and export its canonical result. |

The CLI invokes create_backend() and has no separate application or FMPy logic. Distributed graphs use the documented public Python API rather than a CLI-only path.

## Example review

| Example | Scope |
| --- | --- |
| backend_api_example.py | Public single-FMU workflow. |
| model_exchange_api_example.py | FMI2 Model Exchange workflow. |
| graph_api_example.py | Local two-node graph. |
| distributed_graph_api_example.py | Mixed or two-Worker graph with runtime ExecutionPlan. |
| project_api_example.py | Project lifecycle. |
| phase6_scenario_workflow_example.py | Scenario batch and comparison. |

All Python examples import Farcel only through farcel and farcel.contracts. No example imports farcel.application or farcel.infrastructure.

## Release boundary

The release is a local-first backend with verified localhost distributed graph execution. It does not claim cloud/Internet operation, TLS/authentication, automatic Worker lifecycle, retry/replay/reconnect, checkpoint recovery, dynamic scheduling, Kubernetes, GUI placement editing, strong coupling, FMI3 Model Exchange, or Scheduled Execution.
