"""Public composition root for the default local Farcel backend."""

from farcel.application.engine import FarcelEngine
from farcel.infrastructure.export import CsvGraphResultExporter, CsvResultExporter
from farcel.infrastructure.fmpy import (
    FmpyCvodeSolverFactory,
    FmpyFmi2ModelExchangeSessionFactory,
    FmpyImporter,
    FmpySessionFactory,
)
from farcel.infrastructure.project import (
    LocalJsonProjectRepository,
    LocalJsonProjectRunArtifactRepository,
)


def create_backend() -> FarcelEngine:
    """Create a fully configured local backend using Farcel's default adapters."""

    importer = FmpyImporter()
    return FarcelEngine(
        importer=importer,
        session_factory=FmpySessionFactory(),
        result_exporter=CsvResultExporter(),
        model_exchange_session_factory=FmpyFmi2ModelExchangeSessionFactory(),
        solver_factory=FmpyCvodeSolverFactory(),
        project_repository=LocalJsonProjectRepository(),
        project_run_artifact_repository=LocalJsonProjectRunArtifactRepository(),
        graph_result_exporter=CsvGraphResultExporter(),
    )
