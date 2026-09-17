# Farcel Release Checklist

Use this checklist for a release candidate built from the intended release branch. Each item needs command output or a reviewed CI run; a local green suite does not replace branch-protection policy.

## Source and Git state

- [ ] Record the intended branch and commit SHA.
- [ ] Confirm git status --short is empty before release tagging or publishing.
- [ ] Confirm git diff --check reports no whitespace errors.
- [ ] Confirm no generated file, secret, local FMU cache, virtual environment, or temporary test artifact is included.

## Tests

- [ ] Run python -m pytest in the supported virtual environment.
- [ ] Record collected, passed, failed, and skipped counts.
- [ ] Confirm public backend, graph, distributed graph, project, Worker, protocol, and crash-semantics regressions are included.
- [ ] Confirm example-surface tests permit only farcel and farcel.contracts imports.
- [ ] Confirm real-FMU integration tests run on supported CI Python versions/platforms.

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Expected result: all non-skipped tests pass with zero failures and zero errors.

## Public API

- [ ] create_backend() remains the single default public composition root.
- [ ] SimulationEngine and FarcelEngine signatures remain compatible with the published contract.
- [ ] Local run_graph(graph, config) behavior is unchanged when no plan is supplied.
- [ ] validate_execution_plan(graph, plan) performs no Worker I/O or native runtime creation.
- [ ] Distributed callers use only farcel, farcel.contracts, and keyword-only run_graph(..., execution_plan=plan).
- [ ] No public API exposes FMPy objects, sockets, Worker RPC clients, remote runtime objects, or native handles.

## Architecture

- [ ] contracts imports neither farcel.application nor farcel.infrastructure.
- [ ] application imports no farcel.infrastructure module.
- [ ] infrastructure imports no farcel.application module.
- [ ] FMPy imports remain confined to src/farcel/infrastructure/fmpy/.
- [ ] SimulationOrchestrator, DataRouter, and GraphSimulationRunner remain the sole owners of graph scheduling/routing semantics.
- [ ] No retry, reconnect, replay, Worker restart, cloud execution, or cluster scheduler has been introduced.

```powershell
git grep -n -E '(^|[[:space:]])(from|import)[[:space:]]+farcel\.infrastructure' -- src/farcel/application
git grep -n -E '(^|[[:space:]])(from|import)[[:space:]]+farcel\.application' -- src/farcel/infrastructure
```

Expected result: no matches.

## Documentation and examples

- [ ] ARCHITECTURE_FINAL_REVIEW.md reflects the release boundary.
- [ ] DISTRIBUTED_EXECUTION_USER_GUIDE.md documents local, single Worker, mixed, multi-Worker, failure, and limitation behavior.
- [ ] Phase design records agree with the implemented public boundary.
- [ ] CLI help lists inspect, validate, run, and export.
- [ ] Every example is runnable in its documented context and imports only public Farcel modules.

```powershell
.\.venv\Scripts\python.exe -m farcel.cli --help
```

Expected result: help lists inspect, validate, run, and export.

## CI and release approval

- [ ] Push the reviewed commit to the intended remote branch.
- [ ] Verify the corresponding GitHub Actions run is created and green.
- [ ] Review CI logs for platform-specific FMPy/native or Worker-process failures.
- [ ] Confirm branch protection, required checks, and required human review have completed.
- [ ] Record remaining limitations rather than representing deferred features as supported functionality.
