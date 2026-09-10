from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from farcel.application.runners import (
    CoSimulationRunner,
    ExecutionRunner,
    ModelExchangeRunner,
    validate_result_chunk_size,
)
from farcel.application.graph_runner import GraphSimulationRunner
from farcel.application.graph_runtime_factory import GraphRuntimeBindingsFactory
from farcel.application.graph_validation import GraphValidator
from farcel.application.node_runtime import (
    CoSimulationNodeRuntimeFactory,
    ModelExchangeNodeRuntimeFactory,
)
from farcel.application.project_run_persistence import ProjectRunPersistenceService
from farcel.application.project_batch import ProjectBatchService
from farcel.application.project_comparison import ProjectRunComparisonService
from farcel.application.project_service import ProjectService
from farcel.application.project_validation import ProjectValidator
from farcel.application.validation import resolve_execution_interface, validate_config
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import (
    InterfaceType,
    ModelMetadata,
    ExportReport,
    ResultChunk,
    RunProgress,
    SessionHandle,
    SimulationConfig,
    SimulationResult,
    SimulationState,
    StepResult,
    StepStatus,
    ValidationReport,
)
from farcel.contracts.graph import (
    GraphSimulationConfig,
    GraphSimulationResult,
    SimulationGraph,
)
from farcel.contracts.project import ProjectRunRecord, SimulationProject
from farcel.contracts.project_batch import ProjectBatchProgress, ProjectBatchRunResult
from farcel.contracts.project_comparison import ProjectRunComparison
from farcel.contracts.project_result import ProjectRunArtifact
from farcel.contracts.run_control import RunControl
from farcel.contracts.ports import (
    ModelExchangeSessionFactory,
    ModelImporter,
    ProjectRepository,
    ProjectRunArtifactRepository,
    GraphResultExporter,
    ResultExporter,
    SessionFactory,
    SimulationSession,
    SolverFactory,
)


_MAX_STEP_ATTEMPTS_PER_COMMUNICATION_TARGET = 10000


@dataclass(slots=True)
class _SessionRecord:
    session: SimulationSession
    config: SimulationConfig
    state: SimulationState = SimulationState.CREATED
    current_time: float = 0.0
    next_input_update: int = 0


class FarcelEngine:
    """Application facade shared by CLI and the future GUI."""

    def __init__(
        self,
        importer: ModelImporter,
        session_factory: SessionFactory | None = None,
        result_exporter: ResultExporter | None = None,
        model_exchange_session_factory: ModelExchangeSessionFactory | None = None,
        solver_factory: SolverFactory | None = None,
        project_repository: ProjectRepository | None = None,
        project_run_artifact_repository: ProjectRunArtifactRepository | None = None,
        graph_result_exporter: GraphResultExporter | None = None,
    ) -> None:
        self._importer = importer
        self._project_repository = project_repository
        self._project_run_artifact_repository = project_run_artifact_repository
        self._project_validator = ProjectValidator(importer)
        self._session_factory = session_factory
        self._result_exporter = result_exporter
        self._graph_result_exporter = graph_result_exporter
        self._models: dict[str, ModelMetadata] = {}
        self._sessions: dict[str, _SessionRecord] = {}
        self._co_simulation_runner: ExecutionRunner = CoSimulationRunner(session_factory)
        self._model_exchange_runner: ExecutionRunner = ModelExchangeRunner(
            model_exchange_session_factory, solver_factory
        )
        self._graph_validator = GraphValidator(importer)
        self._graph_runtime_factory = GraphRuntimeBindingsFactory(
            importer,
            CoSimulationNodeRuntimeFactory(session_factory),
            ModelExchangeNodeRuntimeFactory(
                model_exchange_session_factory, solver_factory
            ),
        )

    def load_fmu(self, path: str | Path) -> ModelMetadata:
        metadata = self._importer.load(Path(path))
        self._models[metadata.model_id] = metadata
        return metadata

    def validate_config(
        self, metadata: ModelMetadata, config: SimulationConfig
    ) -> ValidationReport:
        report = validate_config(metadata, config)
        if not report.is_valid:
            raise EngineError(
                ErrorCode.CONFIG_ERROR,
                "仿真配置验证失败",
                self._validation_report_details(report),
            )
        return report

    def validate_graph(
        self,
        graph: SimulationGraph,
        config: GraphSimulationConfig,
    ) -> ValidationReport:
        report = self._graph_validator.validate(graph, config)
        if not report.is_valid:
            raise EngineError(
                ErrorCode.CONFIG_ERROR,
                "Graph 仿真配置验证失败",
                self._validation_report_details(report),
            )
        return report

    def open_project(self, project_root: str | Path) -> SimulationProject:
        repository = self._require_project_repository()
        return repository.load(Path(project_root))

    def save_project(
        self, project_root: str | Path, project: SimulationProject
    ) -> None:
        repository = self._require_project_repository()
        repository.save(Path(project_root), project)

    def validate_project(
        self, project_root: str | Path, project: SimulationProject
    ) -> ValidationReport:
        report = self._project_validator.validate(Path(project_root), project)
        if not report.is_valid:
            raise EngineError(
                ErrorCode.CONFIG_ERROR,
                "项目验证失败",
                self._validation_report_details(report),
            )
        return report

    def load_project_run(
        self,
        project_root: str | Path,
        project: SimulationProject,
        run_id: str,
    ) -> ProjectRunArtifact:
        repository = self._require_project_run_artifact_repository()
        matches = tuple(
            record for record in project.run_history if record.run_id == run_id
        )
        if not matches:
            raise self._project_run_lookup_error(
                "UNKNOWN_RUN_ID", "请求的历史 run_id 不存在"
            )
        if len(matches) > 1:
            raise self._project_run_lookup_error(
                "DUPLICATE_RUN_ID", "run_id 在 run_history 中不唯一"
            )

        record = matches[0]
        expected_path = f"results/{record.run_id}.json"
        if record.result_path != expected_path:
            raise EngineError(
                ErrorCode.PROJECT_FORMAT_ERROR,
                "ProjectRunRecord.result_path 与 run_id 不一致",
                {
                    "run_id": record.run_id,
                    "field": "result_path",
                    "record_value": record.result_path,
                    "expected_value": expected_path,
                },
            )

        artifact = repository.load(Path(project_root), record.result_path)
        self._validate_project_run_artifact(project, record, artifact)
        return artifact

    def run_project_case(
        self,
        project_root: str | Path,
        project: SimulationProject,
        case_id: str,
        *,
        control: RunControl | None = None,
        on_progress: Callable[[RunProgress], None] | None = None,
    ) -> tuple[SimulationProject, ProjectRunRecord, GraphSimulationResult]:
        project_repository = self._require_project_repository()
        artifact_repository = self._require_project_run_artifact_repository()
        root = Path(project_root)
        result = ProjectService(self._project_validator, self.run_graph).run_case(
            root,
            project,
            case_id,
            control=control,
            on_progress=on_progress,
        )
        updated_project, record = ProjectRunPersistenceService(
            project_repository, artifact_repository
        ).persist_run(root, project, case_id, result)
        return updated_project, record, result

    def run_project_cases(
        self,
        project_root: str | Path,
        project: SimulationProject,
        case_ids: tuple[str, ...],
        *,
        control: RunControl | None = None,
        on_progress: Callable[[ProjectBatchProgress], None] | None = None,
    ) -> ProjectBatchRunResult:
        return ProjectBatchService(self.run_project_case).run(
            project_root,
            project,
            case_ids,
            control=control,
            on_progress=on_progress,
        )

    def compare_project_runs(
        self,
        artifacts: tuple[ProjectRunArtifact, ...],
    ) -> ProjectRunComparison:
        return ProjectRunComparisonService().compare(artifacts)

    def create_session(
        self, model_id: str, config: SimulationConfig
    ) -> SessionHandle:
        metadata = self._models.get(model_id)
        if metadata is None:
            raise EngineError(ErrorCode.INTERNAL_ERROR, "模型尚未加载")
        self.validate_config(metadata, config)
        if resolve_execution_interface(metadata, config) is not InterfaceType.CO_SIMULATION:
            raise EngineError(
                ErrorCode.UNSUPPORTED_INTERFACE,
                "低层 Session API 仅支持 Co-Simulation；Model Exchange 请使用 run_fmu()",
            )
        if self._session_factory is None:
            raise EngineError(ErrorCode.NOT_IMPLEMENTED, "未配置仿真 Session 实现")

        concrete_session = self._session_factory.create(metadata, config)
        handle = SessionHandle(session_id=str(uuid4()))
        self._sessions[handle.session_id] = _SessionRecord(
            session=concrete_session, config=config, current_time=config.start_time
        )
        return handle

    def initialize(self, handle: SessionHandle) -> None:
        record = self._get_session(handle)
        if record.state is not SimulationState.CREATED:
            raise EngineError(ErrorCode.INITIALIZATION_ERROR, "Session 状态不允许初始化")
        record.session.initialize()
        record.state = SimulationState.READY

    def step(
        self, handle: SessionHandle, step_size: float | None = None
    ) -> StepResult:
        record = self._get_session(handle)
        if record.state not in {SimulationState.READY, SimulationState.RUNNING}:
            raise EngineError(ErrorCode.STEP_ERROR, "Session 尚未完成初始化")
        current_time = record.current_time
        actual_step = (
            record.config.communication_step if step_size is None else step_size
        )
        self._apply_scheduled_inputs(record, current_time)
        result = record.session.step(current_time, actual_step)
        if result.status is not StepStatus.SUCCESS:
            raise EngineError(ErrorCode.STEP_ERROR, "FMU step 未成功完成")
        if not math.isfinite(result.reached_time) or result.reached_time <= current_time:
            raise EngineError(
                ErrorCode.STEP_ERROR,
                "FMU step 未返回单调递增的 reached time",
                {
                    "current_time": current_time,
                    "requested_time": result.requested_time,
                    "reached_time": result.reached_time,
                    "early_return": result.early_return,
                },
            )
        record.current_time = result.reached_time
        record.state = SimulationState.RUNNING
        return result

    @staticmethod
    def _apply_scheduled_inputs(record: _SessionRecord, current_time: float) -> None:
        schedule = record.config.input_schedule
        if record.next_input_update >= len(schedule):
            return
        update = schedule[record.next_input_update]
        tolerance = max(1e-12, record.config.communication_step * 1e-9)
        if math.isclose(update.time, current_time, rel_tol=0.0, abs_tol=tolerance):
            if update.values:
                record.session.set_inputs(update.values)
            record.next_input_update += 1

    def read_outputs(self, handle: SessionHandle) -> Mapping[str, Any]:
        record = self._get_session(handle)
        if record.state not in {SimulationState.READY, SimulationState.RUNNING}:
            raise EngineError(
                ErrorCode.OUTPUT_READ_ERROR,
                "Session 尚未完成初始化",
            )
        return record.session.read_outputs()

    def terminate(self, handle: SessionHandle) -> None:
        record = self._get_session(handle)
        if record.state is SimulationState.STOPPED:
            return
        record.state = SimulationState.STOPPING
        try:
            record.session.terminate()
        except BaseException:
            record.state = SimulationState.ERROR
            raise
        record.state = SimulationState.STOPPED

    def get_state(self, handle: SessionHandle) -> SimulationState:
        return self._get_session(handle).state

    def close_session(self, handle: SessionHandle) -> None:
        record = self._sessions.pop(handle.session_id, None)
        if record is None:
            return
        record.session.close()

    def run_fmu(
        self,
        path: str | Path,
        config: SimulationConfig,
        *,
        control: RunControl | None = None,
        on_progress: Callable[[RunProgress], None] | None = None,
        on_result_chunk: Callable[[ResultChunk], None] | None = None,
        result_chunk_size: int = 256,
    ) -> SimulationResult:
        validate_result_chunk_size(result_chunk_size)
        if control is not None and control.stop_requested:
            raise EngineError(ErrorCode.CANCELLED, "仿真开始前已请求停止")
        metadata = self.load_fmu(path)
        self.validate_config(metadata, config)
        effective_interface = resolve_execution_interface(metadata, config)
        if effective_interface is None:
            raise EngineError(ErrorCode.INTERNAL_ERROR, "校验后的执行接口无法解析")
        runner = self._select_execution_runner(effective_interface)
        return runner.run(
            path,
            metadata,
            config,
            control=control,
            on_progress=on_progress,
            on_result_chunk=on_result_chunk,
            result_chunk_size=result_chunk_size,
            step_attempt_limit=_MAX_STEP_ATTEMPTS_PER_COMMUNICATION_TARGET,
        )

    def run_graph(
        self,
        graph: SimulationGraph,
        config: GraphSimulationConfig,
        *,
        control: RunControl | None = None,
        on_progress: Callable[[RunProgress], None] | None = None,
    ) -> GraphSimulationResult:
        if control is not None and control.stop_requested:
            raise EngineError(ErrorCode.CANCELLED, "Graph 仿真开始前已请求停止")
        self.validate_graph(graph, config)
        runner = GraphSimulationRunner(
            lambda: self._graph_runtime_factory.create(graph, config)
        )
        return runner.run(graph, config, control=control, on_progress=on_progress)

    def export_result(
        self, result: SimulationResult, destination: str | Path
    ) -> ExportReport:
        if self._result_exporter is None:
            raise EngineError(ErrorCode.NOT_IMPLEMENTED, "未配置结果导出实现")
        return self._result_exporter.export(result, Path(destination))

    def export_graph_result(
        self, result: GraphSimulationResult, destination: str | Path
    ) -> ExportReport:
        if self._graph_result_exporter is None:
            raise EngineError(ErrorCode.NOT_IMPLEMENTED, "未配置 Graph 结果导出实现")
        return self._graph_result_exporter.export(result, Path(destination))

    def _require_project_repository(self) -> ProjectRepository:
        if self._project_repository is None:
            raise EngineError(ErrorCode.NOT_IMPLEMENTED, "未配置项目存储实现")
        return self._project_repository

    def _require_project_run_artifact_repository(
        self,
    ) -> ProjectRunArtifactRepository:
        if self._project_run_artifact_repository is None:
            raise EngineError(
                ErrorCode.NOT_IMPLEMENTED,
                "未配置 ProjectRunArtifactRepository",
            )
        return self._project_run_artifact_repository

    @staticmethod
    def _project_run_lookup_error(issue_code: str, issue_message: str) -> EngineError:
        return EngineError(
            ErrorCode.CONFIG_ERROR,
            "历史运行查询配置无效",
            {
                "issues": (
                    {
                        "field": "run_id",
                        "code": issue_code,
                        "message": issue_message,
                    },
                )
            },
        )

    @staticmethod
    def _validate_project_run_artifact(
        project: SimulationProject,
        record: ProjectRunRecord,
        artifact: ProjectRunArtifact,
    ) -> None:
        checks = (
            ("artifact.run_id", record.run_id, artifact.run_id),
            ("artifact.project_id", project.project_id, artifact.project_id),
            (
                "artifact.case_snapshot.case_id",
                record.case_id,
                artifact.case_snapshot.case_id,
            ),
            (
                "artifact.result.completion_state",
                record.completion_state.value,
                artifact.result.completion_state.value,
            ),
            (
                "artifact.result.final_time",
                record.final_time,
                artifact.result.final_time,
            ),
            (
                "artifact.result.completed_steps",
                record.completed_steps,
                artifact.result.completed_steps,
            ),
        )
        for field, record_value, artifact_value in checks:
            if record_value != artifact_value:
                raise EngineError(
                    ErrorCode.PROJECT_FORMAT_ERROR,
                    "ProjectRunRecord 与 ProjectRunArtifact 不一致",
                    {
                        "run_id": record.run_id,
                        "field": field,
                        "record_value": record_value,
                        "artifact_value": artifact_value,
                    },
                )

    def _get_session(self, handle: SessionHandle) -> _SessionRecord:
        try:
            return self._sessions[handle.session_id]
        except KeyError:
            raise EngineError(ErrorCode.INTERNAL_ERROR, "Session 不存在或已经关闭") from None

    @staticmethod
    def _validation_report_details(report: ValidationReport) -> dict[str, tuple[dict[str, str], ...]]:
        return {
            "issues": tuple(
                {
                    "field": issue.field,
                    "code": issue.code,
                    "message": issue.message,
                }
                for issue in report.issues
            )
        }

    def _select_execution_runner(self, interface: InterfaceType) -> ExecutionRunner:
        if interface is InterfaceType.CO_SIMULATION:
            return self._co_simulation_runner
        if interface is InterfaceType.MODEL_EXCHANGE:
            return self._model_exchange_runner
        raise EngineError(ErrorCode.INTERNAL_ERROR, "校验后的执行接口无法分派")
