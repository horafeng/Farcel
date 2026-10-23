"""Explicit application workflow for Project schema 1.0 to 1.1 migration."""

from __future__ import annotations

from pathlib import Path

from farcel.application.project_validation import ProjectValidator
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.project import (
    PROJECT_SCHEMA_VERSION,
    ProjectSchemaMigrationResult,
)
from farcel.contracts.ports import ProjectSchemaMigrationRepository


class ProjectSchemaMigrationService:
    """Validate and explicitly migrate one persisted Project 1.0 document."""

    def __init__(
        self,
        repository: ProjectSchemaMigrationRepository,
        validator: ProjectValidator,
    ) -> None:
        self._repository = repository
        self._validator = validator

    def migrate_1_0_to_1_1(self, project_root: Path) -> ProjectSchemaMigrationResult:
        candidate = self._repository.load_1_0_migration_candidate(Path(project_root))
        if candidate.project.schema_version != PROJECT_SCHEMA_VERSION:
            raise EngineError(
                ErrorCode.CONFIG_ERROR,
                "仅支持显式迁移 Project schema 1.0。",
                {"schema_version": candidate.project.schema_version},
            )
        report = self._validator.validate(Path(project_root), candidate.project)
        if not report.is_valid:
            raise EngineError(
                ErrorCode.CONFIG_ERROR,
                "迁移前 Project 验证失败。",
                {
                    "issues": tuple(
                        {
                            "field": issue.field,
                            "code": issue.code,
                            "message": issue.message,
                        }
                        for issue in report.issues
                    )
                },
            )
        return self._repository.migrate_1_0_to_1_1(Path(project_root), candidate)
