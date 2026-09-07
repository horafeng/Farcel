"""Application-level path and integrity checks for one project model asset."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath, PureWindowsPath
import re

from farcel.contracts.models import ValidationIssue, ValidationReport
from farcel.contracts.project import ModelAsset


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_HASH_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True, slots=True)
class ProjectAssetCheck:
    resolved_path: Path | None
    report: ValidationReport


class ProjectAssetValidator:
    """Validate one declared project-relative asset without FMI knowledge."""

    def check(self, project_root: Path, asset: ModelAsset) -> ProjectAssetCheck:
        path_issue = _relative_path_issue(asset.relative_path)
        if path_issue is not None:
            return _invalid(path_issue)

        try:
            root_real = Path(project_root).resolve()
            candidate_real = (root_real / asset.relative_path).resolve()
        except (OSError, RuntimeError) as exc:
            return _invalid(
                ValidationIssue(
                    "relative_path",
                    "PROJECT_PATH_ESCAPE",
                    f"无法安全解析 project-relative path: {exc}",
                )
            )

        if not _is_within_root(root_real, candidate_real):
            return _invalid(
                ValidationIssue(
                    "relative_path",
                    "PROJECT_PATH_ESCAPE",
                    "relative_path 解析后不能离开 project_root",
                )
            )

        try:
            if not candidate_real.exists():
                return _invalid(
                    ValidationIssue(
                        "relative_path", "ASSET_MISSING", "ModelAsset 文件不存在"
                    )
                )
            if not candidate_real.is_file():
                return _invalid(
                    ValidationIssue(
                        "relative_path", "ASSET_NOT_FILE", "ModelAsset 必须是普通文件"
                    )
                )
        except OSError as exc:
            return _invalid(
                ValidationIssue(
                    "relative_path", "ASSET_READ_ERROR", f"无法访问 ModelAsset: {exc}"
                )
            )

        if not _is_sha256(asset.sha256):
            return _invalid(
                ValidationIssue(
                    "sha256", "ASSET_SHA256_INVALID", "sha256 必须是 64 位小写十六进制"
                )
            )

        try:
            actual_sha256 = self._sha256(candidate_real)
        except OSError as exc:
            return _invalid(
                ValidationIssue(
                    "relative_path", "ASSET_READ_ERROR", f"无法读取 ModelAsset: {exc}"
                )
            )

        if actual_sha256 != asset.sha256:
            return _invalid(
                ValidationIssue(
                    "sha256", "ASSET_CHECKSUM_MISMATCH", "ModelAsset SHA-256 不匹配"
                )
            )

        return ProjectAssetCheck(candidate_real, ValidationReport())

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(_HASH_CHUNK_SIZE):
                digest.update(chunk)
        return digest.hexdigest()


def _relative_path_issue(relative_path: object) -> ValidationIssue | None:
    if not isinstance(relative_path, str):
        return ValidationIssue(
            "relative_path", "PROJECT_PATH_INVALID", "relative_path 必须是字符串"
        )
    if not relative_path or "\x00" in relative_path or "\\" in relative_path:
        return ValidationIssue(
            "relative_path", "PROJECT_PATH_INVALID", "relative_path 必须使用 canonical / 分隔符"
        )

    posix_path = PurePosixPath(relative_path)
    windows_path = PureWindowsPath(relative_path)
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or windows_path.root
    ):
        return ValidationIssue(
            "relative_path", "PROJECT_PATH_INVALID", "relative_path 不能是绝对路径"
        )

    if any(segment in {"", ".", ".."} for segment in relative_path.split("/")):
        return ValidationIssue(
            "relative_path", "PROJECT_PATH_INVALID", "relative_path 必须是 canonical project-relative path"
        )
    return None


def _is_within_root(root_real: Path, candidate_real: Path) -> bool:
    try:
        candidate_real.relative_to(root_real)
    except ValueError:
        return False
    return True


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def _invalid(issue: ValidationIssue) -> ProjectAssetCheck:
    return ProjectAssetCheck(None, ValidationReport((issue,)))
