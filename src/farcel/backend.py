"""Public composition root for the default Farcel backend."""

from farcel.application.graph_runtime_factory import GraphRuntimeBindingsFactory
from farcel.application.node_runtime import (
    CoSimulationNodeRuntimeFactory,
    ModelExchangeNodeRuntimeFactory,
)
from farcel.application.engine import FarcelEngine
from farcel.application.modelica_service import ModelicaService
from farcel.application.modelica_asset_staging import ModelicaAssetStagingService
from farcel.application.project_modelica_service import ProjectModelicaService
from farcel.application.project_schema_migration import ProjectSchemaMigrationService
from farcel.application.project_validation import ProjectValidator
from farcel.distributed_backend import TcpDistributedGraphExecutor
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
    LocalModelicaAssetStore,
)
from farcel.infrastructure.openmodelica import (
    OpenModelicaCompiler,
    OpenModelicaEnvironmentDetector,
)


def create_backend() -> FarcelEngine:
    """Create the default backend; Worker I/O remains run-time placement driven."""

    importer = FmpyImporter()
    project_repository = LocalJsonProjectRepository()
    compiler = OpenModelicaCompiler()
    project_validator = ProjectValidator(importer)
    session_factory = FmpySessionFactory()
    model_exchange_session_factory = FmpyFmi2ModelExchangeSessionFactory()
    solver_factory = FmpyCvodeSolverFactory()
    distributed_graph_executor = TcpDistributedGraphExecutor(
        GraphRuntimeBindingsFactory(
            importer,
            CoSimulationNodeRuntimeFactory(session_factory),
            ModelExchangeNodeRuntimeFactory(
                model_exchange_session_factory,
                solver_factory,
            ),
        )
    )
    return FarcelEngine(
        importer=importer,
        session_factory=session_factory,
        result_exporter=CsvResultExporter(),
        model_exchange_session_factory=model_exchange_session_factory,
        solver_factory=solver_factory,
        project_repository=project_repository,
        project_run_artifact_repository=LocalJsonProjectRunArtifactRepository(),
        graph_result_exporter=CsvGraphResultExporter(),
        distributed_graph_executor=distributed_graph_executor,
        modelica_service=ModelicaService(
            OpenModelicaEnvironmentDetector(),
            compiler,
        ),
        project_modelica_service=ProjectModelicaService(
            ModelicaAssetStagingService(compiler, LocalModelicaAssetStore()),
            project_repository,
            project_validator,
        ),
        project_schema_migration_service=ProjectSchemaMigrationService(
            project_repository,
            project_validator,
        ),
    )
