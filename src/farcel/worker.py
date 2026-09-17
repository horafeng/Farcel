"""Farcel 内部 localhost Worker 子进程 composition root。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from threading import Event, Thread
from typing import Sequence

from farcel.application.node_runtime import (
    CoSimulationNodeRuntimeFactory,
    ModelExchangeNodeRuntimeFactory,
)
from farcel.application.worker_protocol import WorkerProtocolValidator
from farcel.application.worker_runtime_factory import WorkerRuntimeFactory
from farcel.application.worker_service import WorkerApplicationService
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.ports import WorkerRequestHandler
from farcel.contracts.worker_protocol import WORKER_PROTOCOL_VERSION, WorkerRequest, WorkerResponse
from farcel.infrastructure.fmpy import (
    FmpyCvodeSolverFactory,
    FmpyFmi2ModelExchangeSessionFactory,
    FmpyImporter,
    FmpySessionFactory,
)
from farcel.infrastructure.worker_assets import LocalWorkerAssetCache
from farcel.infrastructure.worker_protocol.json_codec import JsonWorkerProtocolCodec
from farcel.infrastructure.worker_protocol.tcp_transport import TcpWorkerServer


_READINESS_SCHEMA_VERSION = "1.0"


class _ProcessSessionHandler:
    """在 TCP session 结束时关闭 locally-owned Worker process。"""

    def __init__(self, service: WorkerApplicationService, stop_event: Event) -> None:
        self._service = service
        self._stop_event = stop_event

    def handle_request(
        self,
        request: WorkerRequest,
        *,
        binary_payload: bytes | None = None,
    ) -> WorkerResponse:
        return self._service.handle_request(request, binary_payload=binary_payload)

    def shutdown(self) -> None:
        try:
            self._service.shutdown()
        finally:
            self._stop_event.set()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Farcel internal localhost Worker")
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--cache-root", required=True)
    parser.add_argument("--port", type=int, default=0, choices=(0,))
    return parser


def _compose_service(worker_id: str, cache_root: Path) -> WorkerApplicationService:
    asset_store = LocalWorkerAssetCache(cache_root)
    runtime_factory = WorkerRuntimeFactory(
        asset_store,
        FmpyImporter(),
        CoSimulationNodeRuntimeFactory(FmpySessionFactory()),
        ModelExchangeNodeRuntimeFactory(
            FmpyFmi2ModelExchangeSessionFactory(),
            FmpyCvodeSolverFactory(),
        ),
    )
    return WorkerApplicationService(worker_id, asset_store, runtime_factory)


def run_worker(worker_id: str, cache_root: Path, port: int) -> int:
    """组装并运行一个 locally-owned single-session Worker。"""

    if port != 0:
        raise EngineError(ErrorCode.VALIDATION_ERROR, "Worker 只允许使用 port=0")

    validator = WorkerProtocolValidator()
    codec = JsonWorkerProtocolCodec(validator)
    service = _compose_service(worker_id, cache_root)
    stop_event = Event()
    handler: WorkerRequestHandler = _ProcessSessionHandler(service, stop_event)
    server = TcpWorkerServer(
        worker_id,
        handler,
        codec,
        host="127.0.0.1",
        port=port,
    )
    try:
        endpoint = server.start()
        watcher = Thread(
            target=_watch_parent_stdin,
            args=(stop_event, server),
            daemon=True,
        )
        watcher.start()
        readiness = {
            "schema_version": _READINESS_SCHEMA_VERSION,
            "protocol_version": WORKER_PROTOCOL_VERSION,
            "worker_id": worker_id,
            "host": endpoint.host,
            "port": endpoint.port,
        }
        print(json.dumps(readiness, ensure_ascii=False, separators=(",", ":"), sort_keys=True), flush=True)
        server.serve_forever(stop_event=stop_event)
        return 0
    finally:
        server.close()
        try:
            service.shutdown()
        except Exception as exc:
            print(f"Worker shutdown failed: {exc}", file=sys.stderr)


def _watch_parent_stdin(stop_event: Event, server: TcpWorkerServer) -> None:
    """stdin EOF 仅表示 parent-owned Worker lifetime 结束。"""

    try:
        file_descriptor = sys.stdin.fileno()
        while os.read(file_descriptor, 1):
            pass
    except OSError:
        pass
    finally:
        stop_event.set()
        server.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return run_worker(args.worker_id, Path(args.cache_root), args.port)
    except EngineError as exc:
        print(f"Worker startup failed: {exc.code.value}: {exc.message}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Worker startup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
