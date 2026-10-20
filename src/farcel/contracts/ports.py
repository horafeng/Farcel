from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from typing import Any, Mapping, Protocol

from farcel.contracts.distributed import ExecutionPlan
from farcel.contracts.modelica import (
    ModelicaCompileRequest,
    ModelicaCompileResult,
    ModelicaEnvironmentInfo,
)
from farcel.contracts.modelica_provenance import (
    ModelicaAssetStagingRequest,
    ModelicaBuildProvenance,
    ModelicaStagingWorkspace,
    ProjectModelicaCompilation,
)
from farcel.contracts.graph import (
    GraphSimulationConfig,
    GraphSimulationResult,
    SimulationGraph,
)
from farcel.contracts.models import (
    DiscreteStateUpdate,
    ExportReport,
    IntegratorStepResult,
    ModelExchangeInitialization,
    ModelMetadata,
    ResultChunk,
    SessionHandle,
    SimulationConfig,
    SimulationResult,
    SimulationState,
    SolverAdvanceResult,
    SolverOptions,
    SolverResetReason,
    RunProgress,
    StepResult,
    ValidationReport,
)
from farcel.contracts.project import (
    ProjectRevision,
    ProjectSchemaMigrationCandidate,
    ProjectSchemaMigrationResult,
    SimulationProject,
)
from farcel.contracts.project_result import ProjectRunArtifact
from farcel.contracts.run_control import RunControl
from farcel.contracts.worker_protocol import WorkerRequest, WorkerResponse


class ModelImporter(Protocol):
    """Boundary implemented by an FMU technology adapter."""

    def load(self, path: Path) -> ModelMetadata:
        """Parse and normalise an FMU without exposing adapter-native types."""


class ModelicaEnvironmentDetector(Protocol):
    """查询 Modelica 编译器环境的实现无关边界。"""

    def detect(
        self, compiler_path: str | Path | None = None
    ) -> ModelicaEnvironmentInfo:
        """验证指定编译器，或自动发现一个候选编译器。"""


class ModelicaCompiler(Protocol):
    """执行已验证 Modelica 编译请求的实现无关边界。"""

    @property
    def compiler_name(self) -> str:
        """Return the adapter's verified compiler product name."""

    def compile(self, request: ModelicaCompileRequest) -> ModelicaCompileResult:
        """编译一个请求并返回经验证的 FMU 结果。"""


class ModelicaAssetStore(Protocol):
    """Store-owned local workspace and verified build-package lifecycle."""

    def prepare_source_snapshot(
        self,
        project_root: Path,
        source_path: Path,
        build_id: str,
    ) -> ModelicaStagingWorkspace:
        """Create and verify a source snapshot in an unpublished workspace."""

    def verify_source_snapshot(self, workspace: ModelicaStagingWorkspace) -> None:
        """Reject a workspace whose saved source no longer matches its snapshot."""

    def publish_verified_build(
        self,
        workspace: ModelicaStagingWorkspace,
        fmu_path: Path,
        fmu_sha256: str,
    ) -> Path:
        """Publish one verified package and return its project-root-contained FMU."""

    def discard_workspace(self, workspace: ModelicaStagingWorkspace) -> None:
        """Best-effort removal limited to the store-owned unpublished workspace."""

    def discard_published_build(self, project_root: Path, build_id: str) -> None:
        """Remove only a marker-owned build after later validation fails."""


class WorkerProtocolSemanticValidator(Protocol):
    """验证 Worker 协议 DTO 的语义，不依赖具体传输或编码实现。"""

    def validate_request(self, request: WorkerRequest) -> ValidationReport: ...

    def validate_response(
        self,
        response: WorkerResponse,
        *,
        request: WorkerRequest | None = None,
    ) -> ValidationReport: ...


class WorkerAssetStore(Protocol):
    """Worker 本地 content-addressed asset cache 的实现无关边界。"""

    def has_asset(self, sha256: str) -> bool: ...

    def put_asset(self, sha256: str, content: bytes) -> None: ...

    def resolve_asset(self, sha256: str) -> Path: ...


class WorkerRequestHandler(Protocol):
    """处理已解码 Worker DTO 的传输无关 application boundary。"""

    def handle_request(
        self,
        request: WorkerRequest,
        *,
        binary_payload: bytes | None = None,
    ) -> WorkerResponse: ...

    def shutdown(self) -> None: ...


class WorkerTransportClient(Protocol):
    """Coordinator 侧 Worker request/response transport boundary。"""

    def connect(self) -> None: ...

    def request(
        self,
        request: WorkerRequest,
        *,
        binary_payload: bytes | None = None,
    ) -> WorkerResponse: ...

    def close(self) -> None: ...


class SimulationSession(Protocol):
    """Implementation-independent lifecycle of one instantiated FMU."""

    def initialize(self) -> None: ...

    def set_inputs(self, values: Mapping[str, Any]) -> None: ...

    def step(self, current_time: float, step_size: float) -> StepResult: ...

    def read_outputs(self) -> Mapping[str, Any]: ...

    def terminate(self) -> None: ...

    def close(self) -> None: ...


class SessionFactory(Protocol):
    """Create a concrete session behind the Farcel session boundary."""

    def create(
        self, metadata: ModelMetadata, config: SimulationConfig
    ) -> SimulationSession: ...


class ModelExchangeSession(Protocol):
    """Implementation-independent FMI Model Exchange lifecycle."""

    def initialize(self) -> ModelExchangeInitialization: ...
    def get_initial_time(self) -> float: ...
    def get_event_indicator_count(self) -> int: ...
    def set_inputs(self, values: Mapping[str, Any]) -> None: ...
    def set_time(self, time: float) -> None: ...
    def get_continuous_states(self) -> tuple[float, ...]: ...
    def get_nominals_of_continuous_states(self) -> tuple[float, ...]: ...
    def set_continuous_states(self, states: tuple[float, ...]) -> None: ...
    def get_derivatives(self) -> tuple[float, ...]: ...
    def get_event_indicators(self) -> tuple[float, ...]: ...
    def completed_integrator_step(self) -> IntegratorStepResult: ...
    def enter_event_mode(self) -> None: ...
    def update_discrete_states(self) -> DiscreteStateUpdate: ...
    def enter_continuous_time_mode(self) -> None: ...
    def read_outputs(self) -> Mapping[str, Any]: ...
    def terminate(self) -> None: ...
    def close(self) -> None: ...


class ModelExchangeSessionFactory(Protocol):
    """Create a Model Exchange session without adapter-native objects."""

    def create(
        self, metadata: ModelMetadata, config: SimulationConfig
    ) -> ModelExchangeSession: ...


class ModelExchangeProblem(Protocol):
    """Farcel-owned callbacks used by a numerical Model Exchange solver."""

    def get_initial_time(self) -> float: ...
    def get_initial_states(self) -> tuple[float, ...]: ...
    def get_nominals(self) -> tuple[float, ...]: ...
    def get_event_indicator_count(self) -> int: ...
    def set_state(self, time: float, states: tuple[float, ...]) -> None: ...
    def get_derivatives(self) -> tuple[float, ...]: ...
    def get_event_indicators(self) -> tuple[float, ...]: ...


class SolverAdapter(Protocol):
    """Advance a Farcel-owned Model Exchange problem to a target time."""

    def initialize(self, problem: ModelExchangeProblem, options: SolverOptions) -> None: ...
    def integrate_to(self, target_time: float) -> SolverAdvanceResult: ...
    def reset(self, time: float, reason: SolverResetReason) -> None: ...
    def close(self) -> None: ...


class SolverFactory(Protocol):
    """Create a solver adapter selected by a future Model Exchange runner."""

    def create(self) -> SolverAdapter: ...


class ResultExporter(Protocol):
    """Persist an existing canonical result without rerunning a simulation."""

    def export(
        self, result: SimulationResult, destination: Path
    ) -> ExportReport: ...


class GraphResultExporter(Protocol):
    """Persist a canonical graph result without rerunning a simulation."""

    def export(
        self, result: GraphSimulationResult, destination: Path
    ) -> ExportReport: ...


class ProjectRepository(Protocol):
    """Persistence boundary for a SimulationProject directory."""

    def load(self, project_root: Path) -> SimulationProject: ...

    def save(self, project_root: Path, project: SimulationProject) -> None: ...


class ProjectRevisionRepository(Protocol):
    """Optimistic-concurrency persistence boundary for Project mutations."""

    def load_with_revision(self, project_root: Path) -> ProjectRevision: ...

    def save_if_unchanged(
        self,
        project_root: Path,
        project: SimulationProject,
        expected_document_sha256: str,
    ) -> None: ...


class ProjectSchemaMigrationRepository(Protocol):
    """Explicit, repository-owned 1.0 to 1.1 JSON migration boundary."""

    def load_1_0_migration_candidate(
        self, project_root: Path
    ) -> ProjectSchemaMigrationCandidate: ...

    def migrate_1_0_to_1_1(
        self,
        project_root: Path,
        candidate: ProjectSchemaMigrationCandidate,
    ) -> ProjectSchemaMigrationResult: ...


class ProjectRunArtifactRepository(Protocol):
    """Persistence boundary for ProjectRunArtifact files."""

    def save(self, project_root: Path, artifact: ProjectRunArtifact) -> str: ...

    def load(self, project_root: Path, result_path: str) -> ProjectRunArtifact: ...


class SimulationEngine(Protocol):
    """Stable interface consumed by CLI and GUI."""

    def get_modelica_environment(
        self, compiler_path: str | Path | None = None
    ) -> ModelicaEnvironmentInfo: ...

    def compile_modelica(
        self, request: ModelicaCompileRequest
    ) -> ModelicaCompileResult: ...

    def compile_project_modelica(
        self,
        project: SimulationProject,
        request: ModelicaAssetStagingRequest,
    ) -> ProjectModelicaCompilation: ...

    def migrate_project_schema(
        self, project_root: str | Path
    ) -> ProjectSchemaMigrationResult: ...

    def list_project_modelica_builds(
        self, project: SimulationProject
    ) -> tuple[ModelicaBuildProvenance, ...]: ...

    def load_fmu(self, path: str | Path) -> ModelMetadata: ...

    def validate_config(
        self, metadata: ModelMetadata, config: SimulationConfig
    ) -> ValidationReport: ...

    def create_session(
        self, model_id: str, config: SimulationConfig
    ) -> SessionHandle: ...

    def initialize(self, session: SessionHandle) -> None: ...

    def step(
        self, session: SessionHandle, step_size: float | None = None
    ) -> StepResult: ...

    def read_outputs(self, session: SessionHandle) -> Mapping[str, Any]: ...

    def terminate(self, session: SessionHandle) -> None: ...

    def get_state(self, session: SessionHandle) -> SimulationState: ...

    def close_session(self, session: SessionHandle) -> None: ...

    def run_fmu(
        self,
        path: str | Path,
        config: SimulationConfig,
        *,
        control: RunControl | None = None,
        on_progress: Callable[[RunProgress], None] | None = None,
        on_result_chunk: Callable[[ResultChunk], None] | None = None,
        result_chunk_size: int = 256,
    ) -> SimulationResult: ...

    def validate_graph(
        self,
        graph: SimulationGraph,
        config: GraphSimulationConfig,
    ) -> ValidationReport: ...

    def validate_execution_plan(
        self,
        graph: SimulationGraph,
        execution_plan: ExecutionPlan,
    ) -> ValidationReport: ...

    def run_graph(
        self,
        graph: SimulationGraph,
        config: GraphSimulationConfig,
        *,
        control: RunControl | None = None,
        on_progress: Callable[[RunProgress], None] | None = None,
        execution_plan: ExecutionPlan | None = None,
    ) -> GraphSimulationResult: ...

    def export_result(
        self, result: SimulationResult, destination: str | Path
    ) -> ExportReport: ...

    def export_graph_result(
        self, result: GraphSimulationResult, destination: str | Path
    ) -> ExportReport: ...
