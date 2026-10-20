"""Portable project-relative path and digest rules shared by project contracts."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
import re

from farcel.contracts.models import ValidationIssue


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def project_relative_path_issue(relative_path: object) -> ValidationIssue | None:
    """Return the canonical portable-path issue, without accessing the filesystem."""

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


def is_lowercase_sha256(value: object) -> bool:
    """Return whether *value* is a canonical lowercase SHA-256 digest."""

    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None
