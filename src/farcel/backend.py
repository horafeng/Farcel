"""Public composition root for the default local Farcel backend."""

from farcel.application.engine import FarcelEngine
from farcel.infrastructure.export import CsvResultExporter
from farcel.infrastructure.fmpy import FmpyImporter, FmpySessionFactory


def create_backend() -> FarcelEngine:
    """Create a fully configured local backend using Farcel's default adapters."""

    return FarcelEngine(
        importer=FmpyImporter(),
        session_factory=FmpySessionFactory(),
        result_exporter=CsvResultExporter(),
    )
