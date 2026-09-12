"""Coordinator-owned localhost Worker subprocess bootstrap adapter。"""

from __future__ import annotations

import json
import math
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from threading import Lock, Thread
import time
from typing import TextIO

from farcel.contracts.distributed import WorkerDescriptor, WorkerEndpoint
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.worker_protocol import WORKER_PROTOCOL_VERSION


_READINESS_FIELDS = frozenset({"schema_version", "protocol_version", "worker_id", "host", "port"})
_READINESS_SCHEMA_VERSION = "1.0"
_STDERR_TAIL_LIMIT = 8 * 1024


class LocalWorkerSubprocess:
    """启动并拥有一个只绑定 localhost 的 Farcel Worker child process。"""

    def __init__(
        self,
        worker_id: str,
        cache_root: Path,
        *,
        startup_timeout: float = 5.0,
        shutdown_timeout: float = 5.0,
        python_executable: str | Path | None = None,
    ) -> None:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise EngineError(ErrorCode.VALIDATION_ERROR, "worker_id 必须是非空字符串", {"phase": "worker_process"})
        self._worker_id = worker_id
        self._cache_root = Path(cache_root)
        self._startup_timeout = _finite_timeout(startup_timeout, "worker_process_start")
        self._shutdown_timeout = _finite_timeout(shutdown_timeout, "worker_process_shutdown")
        self._python_executable = str(python_executable or sys.executable)
        self._process: subprocess.Popen[str] | None = None
        self._descriptor: WorkerDescriptor | None = None
        self._stderr_tail = ""
        self._stderr_lock = Lock()
        self._stdout_queue: Queue[tuple[str, str]] = Queue()
        self._stdout_thread: Thread | None = None
        self._stderr_thread: Thread | None = None

    @property
    def pid(self) -> int | None:
        return None if self._process is None else self._process.pid

    def start(self) -> WorkerDescriptor:
        if self._descriptor is not None:
            return self._descriptor
        if self._process is not None:
            raise EngineError(ErrorCode.INTERNAL_ERROR, "Worker process 状态无效", {"phase": "worker_process_start"})
        argv = [
            self._python_executable,
            "-m",
            "farcel.worker",
            "--worker-id",
            self._worker_id,
            "--cache-root",
            str(self._cache_root),
            "--port",
            "0",
        ]
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
                bufsize=1,
            )
        except OSError as exc:
            raise EngineError(
                ErrorCode.INTERNAL_ERROR,
                "Worker process 无法启动",
                {"phase": "worker_process_spawn", "issue_code": "WORKER_PROCESS_SPAWN_FAILED", "diagnostic": str(exc)},
            ) from None

        self._process = process
        assert process.stdout is not None and process.stderr is not None
        self._stdout_thread = Thread(target=self._read_readiness, args=(process.stdout,))
        self._stderr_thread = Thread(target=self._drain_stderr, args=(process.stderr,))
        self._stdout_thread.start()
        self._stderr_thread.start()
        try:
            descriptor = self._wait_for_readiness()
        except EngineError:
            self._terminate_and_reap()
            raise
        self._descriptor = descriptor
        return descriptor

    def poll(self) -> int | None:
        return None if self._process is None else self._process.poll()

    def wait(self, timeout: float | None = None) -> int:
        process = self._require_process()
        if timeout is not None:
            timeout = _finite_timeout(timeout, "worker_process_wait")
        try:
            exit_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise EngineError(ErrorCode.TIMEOUT, "等待 Worker process 超时", {"phase": "worker_process_wait", "diagnostic": str(exc)}) from None
        finally:
            if process.poll() is not None:
                self._join_readers()
        return exit_code

    def close(self) -> None:
        if self._process is None:
            return
        self._terminate_and_reap()

    def _wait_for_readiness(self) -> WorkerDescriptor:
        process = self._require_process()
        deadline = time.monotonic() + self._startup_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EngineError(
                    ErrorCode.TIMEOUT,
                    "等待 Worker readiness 超时",
                    {"phase": "worker_process_start", "issue_code": "WORKER_PROCESS_START_TIMEOUT"},
                )
            try:
                kind, value = self._stdout_queue.get(timeout=min(remaining, 0.05))
            except Empty:
                exit_code = process.poll()
                if exit_code is not None:
                    raise self._early_exit_error(exit_code)
                continue
            if kind == "line":
                if value == "" and process.poll() is not None:
                    raise self._early_exit_error(process.poll())
                try:
                    return _parse_readiness(value, self._worker_id)
                except EngineError:
                    raise
                except Exception as exc:
                    raise _readiness_error(str(exc)) from None
            if kind == "decode_error":
                raise _readiness_error(value)
            exit_code = process.poll()
            if exit_code is not None:
                raise self._early_exit_error(exit_code)

    def _early_exit_error(self, exit_code: int) -> EngineError:
        details: dict[str, object] = {
            "phase": "worker_process_start",
            "issue_code": "WORKER_PROCESS_EXITED_BEFORE_READY",
            "exit_code": exit_code,
        }
        diagnostic = self._stderr_diagnostic()
        if diagnostic:
            details["diagnostic"] = diagnostic
        return EngineError(ErrorCode.INTERNAL_ERROR, "Worker process 在 readiness 前退出", details)

    def _read_readiness(self, stream: TextIO) -> None:
        try:
            self._stdout_queue.put(("line", stream.readline()))
        except UnicodeDecodeError as exc:
            self._stdout_queue.put(("decode_error", str(exc)))
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _drain_stderr(self, stream: TextIO) -> None:
        try:
            while chunk := stream.read(1024):
                with self._stderr_lock:
                    self._stderr_tail = (self._stderr_tail + chunk)[-_STDERR_TAIL_LIMIT:]
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _stderr_diagnostic(self) -> str:
        with self._stderr_lock:
            return self._stderr_tail.strip()

    def _terminate_and_reap(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=self._shutdown_timeout)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
                try:
                    process.wait(timeout=self._shutdown_timeout)
                except subprocess.TimeoutExpired:
                    pass
        self._join_readers()

    def _join_readers(self) -> None:
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not None:
                thread.join(self._shutdown_timeout)

    def _require_process(self) -> subprocess.Popen[str]:
        if self._process is None:
            raise EngineError(ErrorCode.INTERNAL_ERROR, "Worker process 尚未启动", {"phase": "worker_process"})
        return self._process


def _parse_readiness(line: str, expected_worker_id: str) -> WorkerDescriptor:
    try:
        document = json.loads(line)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _readiness_error(str(exc)) from None
    if not isinstance(document, dict) or set(document) != _READINESS_FIELDS:
        raise _readiness_error("readiness fields 无效")
    if document["schema_version"] != _READINESS_SCHEMA_VERSION:
        raise _readiness_error("schema_version 不匹配")
    if document["protocol_version"] != WORKER_PROTOCOL_VERSION:
        raise _readiness_error("protocol_version 不匹配")
    if document["worker_id"] != expected_worker_id:
        raise _readiness_error("worker_id 不匹配")
    if document["host"] != "127.0.0.1":
        raise _readiness_error("host 不允许")
    port = document["port"]
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise _readiness_error("port 无效")
    return WorkerDescriptor(expected_worker_id, WorkerEndpoint("127.0.0.1", port))


def _readiness_error(diagnostic: str) -> EngineError:
    return EngineError(
        ErrorCode.INTERNAL_ERROR,
        "Worker readiness 无效",
        {"phase": "worker_process_readiness", "issue_code": "WORKER_PROCESS_READINESS_INVALID", "diagnostic": diagnostic},
    )


def _finite_timeout(value: object, phase: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise EngineError(ErrorCode.VALIDATION_ERROR, "timeout 必须是正的有限数", {"phase": phase})
    return float(value)
