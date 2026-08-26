# Farcel Development Instructions

## 1. Project Overview

Farcel is a lightweight local FMU simulation application based on the FMI standard.

The first release focuses on:

- FMI 2.0 Co-Simulation
- Basic FMI 3.0 Co-Simulation
- FMU import and inspection
- Model metadata parsing
- Simulation configuration
- Simulation execution
- Result collection and visualization support
- CSV export

The current backend implementation uses Python and FMPy.

The architecture must preserve the ability to replace the Python/FMPy simulation backend with a future C/C++ implementation without requiring major changes to the GUI or application layer.

---

## 2. Architecture Boundary

The intended dependency direction is:

```text
GUI / CLI
    ↓
application
    ↓
contracts
    ↑
infrastructure
```

The application layer depends on Farcel contracts, not on concrete infrastructure implementations.

Concrete simulation implementations satisfy Farcel-defined contracts.

Dependency inversion must be preserved.

---

## 3. FMPy Isolation Rule

FMPy is an implementation detail.

FMPy-specific imports, objects and behaviors must remain inside:

```text
src/farcel/infrastructure/fmpy/
```

The following areas must not import FMPy:

```text
src/farcel/contracts/
src/farcel/application/
GUI code
```

Public APIs must use only:

- Farcel-owned dataclasses
- Farcel-owned enums
- Farcel error models
- Standard Python types

Do not expose FMPy classes or objects through public Farcel interfaces.

Examples of objects that must not leak outside the FMPy adapter include FMPy model description objects, FMU instance objects and FMPy-specific result structures.

---

## 4. Current FMI Scope

### MVP execution scope

Farcel currently targets:

- FMI 2.0 Co-Simulation
- Basic FMI 3.0 Co-Simulation

"Basic FMI 3.0 Co-Simulation" does not imply complete support for every FMI 3.0 advanced capability.

Advanced FMI 3 features may be added incrementally.

### Inspection-only scope

The following interfaces may be detected, parsed and displayed, but are not currently executed:

- FMI 2.0 Model Exchange
- FMI 3.0 Model Exchange
- FMI 3.0 Scheduled Execution

Farcel must clearly distinguish:

```text
FMU can be parsed
```

from:

```text
FMU can currently be executed by Farcel
```

An unsupported execution interface must not cause metadata inspection to fail unnecessarily.

---

## 5. Deferred Features

Do not implement the following unless explicitly requested:

- Model Exchange solver
- Scheduled Execution runtime
- Multi-FMU simulation
- Co-simulation master algorithms
- Distributed simulation
- Worker processes
- RPC
- Database persistence
- Plugin systems
- Network services
- Cloud execution
- Automatic FMU source compilation
- Complex task schedulers

Do not add speculative architecture for these features.

Prefer the smallest design that satisfies the current milestone while preserving existing architectural boundaries.

---

## 6. Simulation Engine Boundary

Simulation behavior must be exposed through Farcel-owned contracts.

The architecture must allow a future implementation such as:

```text
Python / FMPy
```

to be replaced by:

```text
C / C++
```

without forcing the GUI or high-level application workflow to understand the implementation technology.

Avoid coupling public contracts to:

- Python-specific implementation details
- FMPy lifecycle objects
- ctypes handles
- native library handles
- FMI implementation-specific classes

---

## 7. Error Handling

Infrastructure exceptions must not leak directly into the public application interface.

Convert implementation-specific failures into Farcel's stable error model.

Errors should be understandable at the application and GUI level.

Do not report success when an operation has not actually been implemented.

Unsupported functionality must fail explicitly and predictably.

Do not silently fall back to fake results or mocked execution.

---

## 8. Compatibility and Contract Changes

Treat the contents of:

```text
src/farcel/contracts/
```

as the stable boundary between major parts of the application.

Contract changes are allowed when necessary, especially while the project is still early, but they must be:

- minimal
- justified by the current milestone
- implementation-independent
- compatible with future simulation backends

Do not redesign public contracts merely to match FMPy's internal API.

Before making a significant contract change, determine whether the requirement belongs to Farcel itself or only to the current FMPy implementation.

---

## 9. Testing Requirements

Every completed milestone must include automated tests appropriate to the new behavior.

Maintain tests for architectural dependency boundaries.

Where practical, test using real FMUs in addition to synthetic fixtures.

Tests must not merely confirm that functions can be called; they should verify externally meaningful behavior.

Existing passing tests should remain passing unless an intentional contract change makes an update necessary.

---

## 10. CLI Role

The CLI is a development and verification interface for the backend.

The GUI and CLI must use the same application/backend capabilities rather than implementing separate simulation logic.

Where practical, backend milestones should expose a simple CLI path that allows functionality to be manually verified without requiring the GUI.

Do not place core simulation logic inside CLI command handlers.

---

## 11. Development Style

Prefer:

- clear module boundaries
- small incremental changes
- readable Python
- explicit behavior
- stable contracts
- testable components

Avoid:

- premature abstraction
- unnecessary design patterns
- deep inheritance hierarchies
- unnecessary factories/managers/providers
- large speculative frameworks
- duplicate execution paths
- hidden global state

Do not increase architectural complexity unless the current requirement clearly needs it.

---

## 12. Current Project State

The project has already completed:

### Milestone 1 — Backend Skeleton

- contracts
- application layer
- infrastructure boundary
- CLI skeleton
- error model
- basic tests

### Milestone 2 — Real FMU Metadata Inspection

The following real chain is working:

```text
FMU
 ↓
FMPy
 ↓
Farcel ModelMetadata
 ↓
CLI inspect
```

Current capabilities include:

- FMI 2.0 metadata inspection
- FMI 3.0 metadata inspection support
- Co-Simulation / Model Exchange / Scheduled Execution detection
- default experiment parsing
- variable parsing
- platform and capability detection
- executable capability determination
- human-readable CLI inspection
- JSON inspection output
- stable error mapping

Real simulation execution has not yet been implemented.

---

## 13. Current Execution Policy

An FMU is currently considered executable by the Farcel MVP only when the current implementation supports its execution requirements.

At minimum, current execution policy considers:

- supported FMI version
- Co-Simulation availability
- compatible current-platform binary
- external execution-tool requirements

This is a Farcel MVP capability policy, not a statement that other FMUs are inherently invalid according to FMI.

FMUs outside the current execution policy may still be successfully parsed and inspected.

---

## 14. Work Discipline

For each requested task:

1. Read this `AGENTS.md`.
2. Read relevant project documentation before changing architecture.
3. Inspect the existing implementation before adding new abstractions.
4. Preserve architecture boundaries.
5. Implement only the requested milestone.
6. Add or update tests.
7. Run the relevant automated test suite.
8. Provide a concise summary of changed files and behavior.
9. Provide exact verification commands that the developer can run locally.
10. State the expected result of each verification command.

Unless explicitly requested:

- do not commit
- do not push
- do not create releases
- do not implement the next milestone automatically

Stop after the requested milestone is complete and verified.