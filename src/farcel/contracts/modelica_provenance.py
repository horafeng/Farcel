"""Implementation-independent Modelica source and FMU build provenance contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from farcel.contracts.project import ModelAsset


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


@dataclass(frozen=True, slots=True)
class ModelicaAssetStagingRequest:
    """One local source-to-asset staging request; it is never persisted."""

    project_root: str | Path
    source_path: str | Path
    model_name: str
    compiler_path: str | Path | None = None
    fmi_version: str = "2.0"
    fmu_type: str = "cs"
    timeout_seconds: float = 120.0


@dataclass(frozen=True, slots=True)
class ModelicaStagingWorkspace:
    """Runtime-only paths for one store-owned, unpublished build workspace."""

    build_id: str
    project_root: Path
    workspace_path: Path
    source_path: Path
    compiler_output_directory: Path
    source_snapshot: ModelicaSourceSnapshot
    fmu_relative_path: str


@dataclass(frozen=True, slots=True)
class ModelicaStagedBuild:
    """Verified local assets that have not been registered in project.json."""

    model_asset: ModelAsset
    provenance: ModelicaBuildProvenance
