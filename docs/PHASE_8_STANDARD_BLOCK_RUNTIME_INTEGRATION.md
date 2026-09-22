# Phase 8.5.1 — Standard Block Runtime Integration Foundation

## Purpose

Phase 8.5.1 provides the first bridge from a registered standard-block
descriptor to the existing graph-facing `ModelNode` contract:

```text
BlockDescriptor
    ↓
StandardBlockCatalog
    ↓
StandardBlockFactory
    ↓
ModelNode
```

`BlockDescriptor.block_id` identifies the reusable standard block. The caller
supplies `node_id` because that identifies one occurrence of the block in a
specific `SimulationGraph`.

## Application-layer responsibility

`StandardBlockFactory` lives in `farcel.application` because it coordinates
two Farcel-owned public capabilities: catalog lookup and construction of an
existing graph contract. It uses Python package resources to resolve the
descriptor's `fmu_asset` to the local path already expected by `ModelNode`.

The factory has no FMPy import, does not inspect FMU archives, and does not
introduce a second FMU loader or simulator. The normal backend and graph
runtime continue to own FMU parsing, instantiation, stepping, and result
collection.

## Runtime scope

The returned `ModelNode` keeps the standard block's resolved FMU path,
parameter overrides, and declared execution interface in its existing
`ModelNodeConfig`. It can therefore be added to an existing
`SimulationGraph` through the established APIs.

This phase intentionally makes no changes to the FMU runtime, solver,
`SimulationGraph` implementation, Worker protocol, project schema, or GUI.
It is an adapter/factory foundation only; graph creation and graphical block
authoring remain later work.
