"""Implementation-independent Modelica source and FMU build provenance contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelicaSourceSnapshot:
    """One immutable project-relative Modelica source-content identity."""

    relative_path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ModelicaBuildProvenance:
    """Trace a project FMU asset to a source snapshot and stable compile shape.

    ``ModelAsset.sha256`` remains the sole authority for the generated FMU
    contents; this contract intentionally records no second FMU digest.
    """

    build_id: str
    source_snapshot: ModelicaSourceSnapshot
    model_name: str
    compiler_name: str
    compiler_version: str
    fmi_version: str
    fmu_type: str
    fmu_asset_id: str
