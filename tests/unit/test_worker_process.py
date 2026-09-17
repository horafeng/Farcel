from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.infrastructure.worker_process import LocalWorkerSubprocess, _parse_readiness


class _BlockingStream:
    def __init__(self) -> None:
        self._released = Event()

    def readline(self) -> str:
        self._released.wait(1)
        return ""

    def close(self) -> None:
        self._released.set()


class _FakeStdin:
    def __init__(self) -> None:
        self.closed_by_parent = False

    def close(self) -> None:
        self.closed_by_parent = True


class _FakeProcess:
    def __init__(self, stdout: object, *, returncode: int | None = None, wait_results: tuple[object, ...] = ()) -> None:
        self.stdin = _FakeStdin()
        self.stdout = stdout
        self.stderr = io.StringIO("")
        self.returncode = returncode
        self.pid = 4242
        self.terminated = False
        self.killed = False
        self._wait_results = list(wait_results)

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        if self._wait_results:
            result = self._wait_results.pop(0)
            if isinstance(result, BaseException):
                raise result
            self.returncode = int(result)
            return self.returncode
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class WorkerReadinessTests(unittest.TestCase):
    def test_parse_readiness_requires_exact_valid_localhost_schema(self) -> None:
        line = json.dumps({"schema_version": "1.0", "protocol_version": "1.0", "worker_id": "worker-a", "host": "127.0.0.1", "port": 12345})
        descriptor = _parse_readiness(line, "worker-a")
        self.assertEqual(descriptor.worker_id, "worker-a")
        self.assertEqual((descriptor.endpoint.host, descriptor.endpoint.port), ("127.0.0.1", 12345))

    def test_parse_readiness_rejects_invalid_schema_values(self) -> None:
        valid = {"schema_version": "1.0", "protocol_version": "1.0", "worker_id": "worker-a", "host": "127.0.0.1", "port": 12345}
        invalid_documents: list[object] = [
            "not json", [], {key: value for key, value in valid.items() if key != "port"},
            {**valid, "extra": True}, {**valid, "schema_version": "2.0"},
            {**valid, "protocol_version": "2.0"}, {**valid, "worker_id": "worker-b"},
            {**valid, "host": "0.0.0.0"}, {**valid, "port": 0},
            {**valid, "port": 65536}, {**valid, "port": True}, {**valid, "port": "12345"},
        ]
        for document in invalid_documents:
            with self.subTest(document=document):
                line = document if isinstance(document, str) else json.dumps(document)
                with self.assertRaises(EngineError) as raised:
                    _parse_readiness(line, "worker-a")
                self.assertEqual(raised.exception.code, ErrorCode.INTERNAL_ERROR)
                self.assertEqual(raised.exception.details["issue_code"], "WORKER_PROCESS_READINESS_INVALID")


class LocalWorkerSubprocessTests(unittest.TestCase):
    def _launcher(self, temporary_directory: str, **kwargs: object) -> LocalWorkerSubprocess:
        return LocalWorkerSubprocess("worker-a", Path(temporary_directory), startup_timeout=0.05, shutdown_timeout=0.05, **kwargs)

    @staticmethod
    def _timeout() -> subprocess.TimeoutExpired:
        return subprocess.TimeoutExpired("worker", 0.05)

    def _attach(self, launcher: LocalWorkerSubprocess, process: _FakeProcess) -> None:
        launcher._process = process

    def test_timeout_values_require_positive_finite_numbers(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            for value in (0, -1, True, float("nan"), float("inf"), float("-inf"), "1"):
                for keyword in ("startup_timeout", "shutdown_timeout"):
                    with self.subTest(value=value, keyword=keyword):
                        with self.assertRaises(EngineError) as raised:
                            LocalWorkerSubprocess("worker-a", Path(temporary_directory), **{keyword: value})
                        self.assertEqual(raised.exception.code, ErrorCode.VALIDATION_ERROR)

    def test_spawn_failure_is_stable_and_has_no_child(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            launcher = LocalWorkerSubprocess("worker-a", Path(temporary_directory), python_executable=Path(temporary_directory) / "missing-python.exe")
            with self.assertRaises(EngineError) as raised:
                launcher.start()
            self.assertEqual(raised.exception.code, ErrorCode.INTERNAL_ERROR)
            self.assertEqual(raised.exception.details["issue_code"], "WORKER_PROCESS_SPAWN_FAILED")
            self.assertIsNone(launcher.pid)

    def test_startup_timeout_uses_ownership_cleanup(self) -> None:
        fake = _FakeProcess(_BlockingStream())
        with TemporaryDirectory() as temporary_directory:
            launcher = self._launcher(temporary_directory)
            with patch("farcel.infrastructure.worker_process.subprocess.Popen", return_value=fake):
                with self.assertRaises(EngineError) as raised:
                    launcher.start()
            self.assertEqual(raised.exception.code, ErrorCode.TIMEOUT)
            self.assertEqual(raised.exception.details["issue_code"], "WORKER_PROCESS_START_TIMEOUT")
            self.assertTrue(fake.stdin.closed_by_parent)

    def test_early_exit_and_invalid_readiness_are_stable_and_reaped(self) -> None:
        for fake, expected in (
            (_FakeProcess(io.StringIO(""), returncode=7), "WORKER_PROCESS_EXITED_BEFORE_READY"),
            (_FakeProcess(io.StringIO("not json\n")), "WORKER_PROCESS_READINESS_INVALID"),
        ):
            with self.subTest(expected=expected), TemporaryDirectory() as temporary_directory:
                launcher = self._launcher(temporary_directory)
                with patch("farcel.infrastructure.worker_process.subprocess.Popen", return_value=fake):
                    with self.assertRaises(EngineError) as raised:
                        launcher.start()
                self.assertEqual(raised.exception.code, ErrorCode.INTERNAL_ERROR)
                self.assertEqual(raised.exception.details["issue_code"], expected)
                if expected == "WORKER_PROCESS_READINESS_INVALID":
                    self.assertTrue(fake.stdin.closed_by_parent)

    def test_close_prefers_stdin_eof_without_terminate_or_kill(self) -> None:
        fake = _FakeProcess(io.StringIO(""), wait_results=(0,))
        with TemporaryDirectory() as temporary_directory:
            launcher = self._launcher(temporary_directory)
            self._attach(launcher, fake)
            launcher.close()
            self.assertTrue(fake.stdin.closed_by_parent)
            self.assertFalse(fake.terminated)
            self.assertFalse(fake.killed)
            launcher.close()
            self.assertFalse(fake.terminated)
            self.assertFalse(fake.killed)

    def test_close_uses_terminate_only_after_graceful_timeout(self) -> None:
        fake = _FakeProcess(io.StringIO(""), wait_results=(self._timeout(), 0))
        with TemporaryDirectory() as temporary_directory:
            launcher = self._launcher(temporary_directory)
            self._attach(launcher, fake)
            launcher.close()
            self.assertTrue(fake.stdin.closed_by_parent)
            self.assertTrue(fake.terminated)
            self.assertFalse(fake.killed)

    def test_close_uses_kill_only_after_terminate_timeout(self) -> None:
        fake = _FakeProcess(io.StringIO(""), wait_results=(self._timeout(), self._timeout(), 0))
        with TemporaryDirectory() as temporary_directory:
            launcher = self._launcher(temporary_directory)
            self._attach(launcher, fake)
            launcher.close()
            self.assertTrue(fake.stdin.closed_by_parent)
            self.assertTrue(fake.terminated)
            self.assertTrue(fake.killed)

    def test_close_reports_cleanup_failure_after_all_fallbacks(self) -> None:
        fake = _FakeProcess(io.StringIO(""), wait_results=(self._timeout(), self._timeout(), self._timeout()))
        with TemporaryDirectory() as temporary_directory:
            launcher = self._launcher(temporary_directory)
            self._attach(launcher, fake)
            with self.assertRaises(EngineError) as raised:
                launcher.close()
            self.assertEqual(raised.exception.code, ErrorCode.CLEANUP_ERROR)
            self.assertEqual(raised.exception.details["issue_code"], "WORKER_PROCESS_CLEANUP_FAILED")
            self.assertTrue(fake.terminated)
            self.assertTrue(fake.killed)
