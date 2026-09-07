"""Atomic filesystem storage for portable project run artifacts."""

from __future__ import annotations

import os
from pathlib import Path
import re
import tempfile

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.project_result import ProjectRunArtifact
from farcel.infrastructure.project.result_codec import JsonProjectRunArtifactCodec


_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class LocalJsonProjectRunArtifactRepository:
    """Store immutable artifacts as canonical ``results/<run_id>.json`` files."""

    def __init__(self, codec: JsonProjectRunArtifactCodec | None = None) -> None:
        self._codec = codec or JsonProjectRunArtifactCodec()

    def save(self, project_root: Path, artifact: ProjectRunArtifact) -> str:
        run_id = _safe_run_id(artifact.run_id)
        result_path = f"results/{run_id}.json"
        text = self._codec.dumps(artifact)
        results_directory, target = _save_target(project_root, run_id)
        temporary_path: Path | None = None
        try:
            results_directory.mkdir(parents=True, exist_ok=True)
            _ensure_contained(results_directory.parent, results_directory, target)
            if target.exists():
                raise OSError("result artifact already exists")
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=results_directory,
                prefix=".result-",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            if target.exists():
                raise OSError("result artifact already exists")
            os.replace(temporary_path, target)
            temporary_path = None
        except OSError as exc:
            raise _io_error("无法原子保存 result artifact", target, exc) from None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
        return result_path

    def load(self, project_root: Path, result_path: str) -> ProjectRunArtifact:
        run_id = _run_id_from_result_path(result_path)
        _, target = _load_target(project_root, run_id)
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise _format_error("result artifact 不是有效 UTF-8", target, exc) from None
        except OSError as exc:
            raise _io_error("无法读取 result artifact", target, exc) from None

        artifact = self._codec.loads(text)
        expected_path = f"results/{artifact.run_id}.json"
        if result_path != expected_path:
            raise _format_error(
                "result_path 与 artifact.run_id 不一致", target, ValueError(expected_path)
            )
        return artifact


def _safe_run_id(run_id: object) -> str:
    if not isinstance(run_id, str) or not _RUN_ID_PATTERN.fullmatch(run_id):
        raise _format_error("run_id 不是安全的 portable filename stem", Path(str(run_id)), ValueError())
    if run_id in {".", ".."} or run_id.endswith((".", " ")):
        raise _format_error("run_id 不是安全的 portable filename stem", Path(run_id), ValueError())
    return run_id


def _run_id_from_result_path(result_path: object) -> str:
    if not isinstance(result_path, str) or "\\" in result_path or "\x00" in result_path:
        raise _format_error("result_path 必须是 canonical results path", Path(str(result_path)), ValueError())
    prefix = "results/"
    suffix = ".json"
    if not result_path.startswith(prefix) or not result_path.endswith(suffix):
        raise _format_error("result_path 必须是 canonical results path", Path(result_path), ValueError())
    run_id = result_path[len(prefix) : -len(suffix)]
    if result_path != f"results/{run_id}.json":
        raise _format_error("result_path 必须是 canonical results path", Path(result_path), ValueError())
    return _safe_run_id(run_id)


def _save_target(project_root: Path, run_id: str) -> tuple[Path, Path]:
    root = Path(project_root).resolve()
    results_directory = root / "results"
    target = results_directory / f"{run_id}.json"
    _ensure_contained(root, results_directory, target)
    return results_directory, target


def _load_target(project_root: Path, run_id: str) -> tuple[Path, Path]:
    root = Path(project_root).resolve()
    results_directory = root / "results"
    target = results_directory / f"{run_id}.json"
    _ensure_contained(root, results_directory, target)
    return results_directory, target


def _ensure_contained(project_root: Path, results_directory: Path, target: Path) -> None:
    try:
        results_real = results_directory.resolve()
        target_real = target.resolve()
    except (OSError, RuntimeError) as exc:
        raise _format_error("无法安全解析 result artifact path", target, exc) from None
    if not _is_within(project_root, results_real) or not _is_within(results_real, target_real):
        raise _format_error("result artifact path 不能离开 project_root/results", target, ValueError())


def _is_within(parent: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


def _format_error(message: str, path: Path, cause: Exception) -> EngineError:
    return EngineError(
        ErrorCode.PROJECT_FORMAT_ERROR,
        message,
        {"path": str(path), "diagnostic": str(cause)},
    )


def _io_error(message: str, path: Path, cause: Exception) -> EngineError:
    return EngineError(
        ErrorCode.PROJECT_IO_ERROR,
        message,
        {"path": str(path), "diagnostic": str(cause)},
    )
