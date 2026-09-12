"""Worker 单节点 runtime 的 application-level 构造。"""

from __future__ import annotations

from farcel.application.node_runtime import (
    CoSimulationNodeRuntimeFactory,
    ModelExchangeNodeRuntimeFactory,
    ModelNodeRuntime,
)
from farcel.application.validation import resolve_execution_interface, validate_config
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import InterfaceType
from farcel.contracts.ports import ModelImporter, WorkerAssetStore
from farcel.contracts.worker_protocol import CreateRuntimeRequest


class WorkerRuntimeFactory:
    """从 Worker 本地 asset cache 创建未初始化的单节点 runtime。"""

    def __init__(
        self,
        asset_store: WorkerAssetStore,
        importer: ModelImporter,
        co_simulation_factory: CoSimulationNodeRuntimeFactory,
        model_exchange_factory: ModelExchangeNodeRuntimeFactory,
    ) -> None:
        self._asset_store = asset_store
        self._importer = importer
        self._co_simulation_factory = co_simulation_factory
        self._model_exchange_factory = model_exchange_factory

    def create(self, request: CreateRuntimeRequest) -> ModelNodeRuntime:
        """用 SHA-256 所定位的 Worker 本机 asset 创建 runtime。"""

        try:
            asset_path = self._asset_store.resolve_asset(request.asset_sha256)
            metadata = self._importer.load(asset_path)
            report = validate_config(metadata, request.config)
            if not report.is_valid:
                raise EngineError(
                    ErrorCode.CONFIG_ERROR,
                    "Worker runtime 的 SimulationConfig 无效",
                    {
                        "phase": "worker_runtime_creation",
                        "node_id": request.node_id,
                        "asset_sha256": request.asset_sha256,
                        "issues": tuple(
                            {
                                "field": issue.field,
                                "code": issue.code,
                                "message": issue.message,
                            }
                            for issue in report.issues
                        ),
                    },
                )

            interface = resolve_execution_interface(metadata, request.config)
            if interface is InterfaceType.CO_SIMULATION:
                return self._co_simulation_factory.create(metadata, request.config)
            if interface is InterfaceType.MODEL_EXCHANGE:
                return self._model_exchange_factory.create(metadata, request.config)
            raise EngineError(
                ErrorCode.UNSUPPORTED_INTERFACE,
                "Worker runtime 不支持所请求的执行接口",
                {
                    "phase": "worker_runtime_creation",
                    "node_id": request.node_id,
                    "asset_sha256": request.asset_sha256,
                },
            )
        except EngineError:
            raise
        except Exception as exc:
            raise EngineError(
                ErrorCode.INTERNAL_ERROR,
                "Worker runtime 创建失败",
                {
                    "phase": "worker_runtime_creation",
                    "node_id": request.node_id,
                    "asset_sha256": request.asset_sha256,
                    "diagnostic": str(exc),
                },
            ) from None
