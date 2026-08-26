from __future__ import annotations

from pathlib import Path

from farcel.application.validation import validate_config
from farcel.contracts.models import ModelMetadata, SimulationConfig, ValidationReport
from farcel.contracts.ports import ModelImporter


class FarcelEngine:
    """Small application facade shared by CLI and the future GUI."""

    def __init__(self, importer: ModelImporter) -> None:
        self._importer = importer

    def load_fmu(self, path: str | Path) -> ModelMetadata:
        return self._importer.load(Path(path))

    def validate_config(
        self, metadata: ModelMetadata, config: SimulationConfig
    ) -> ValidationReport:
        return validate_config(metadata, config)

