from pathlib import Path
import unittest

from farcel.application.engine import FarcelEngine
from farcel.contracts import EngineError, ErrorCode, SimulationProject


class _Importer:
    def load(self, path: Path):
        raise AssertionError(f"Unexpected model import: {path}")


class _ProjectRepository:
    def __init__(self, project: SimulationProject) -> None:
        self.project = project
        self.loaded_roots: list[Path] = []
        self.saved: list[tuple[Path, SimulationProject]] = []
        self.load_error: EngineError | None = None
        self.save_error: EngineError | None = None

    def load(self, project_root: Path) -> SimulationProject:
        self.loaded_roots.append(project_root)
        if self.load_error is not None:
            raise self.load_error
        return self.project

    def save(self, project_root: Path, project: SimulationProject) -> None:
        self.saved.append((project_root, project))
        if self.save_error is not None:
            raise self.save_error


class ProjectBackendApiTests(unittest.TestCase):
    def test_open_and_save_delegate_without_semantic_validation(self) -> None:
        invalid_project = SimulationProject("", "Demo")
        repository = _ProjectRepository(invalid_project)
        engine = FarcelEngine(_Importer(), project_repository=repository)

        engine.save_project("project-root", invalid_project)
        opened = engine.open_project("project-root")

        self.assertIs(opened, invalid_project)
        self.assertEqual(repository.saved, [(Path("project-root"), invalid_project)])
        self.assertEqual(repository.loaded_roots, [Path("project-root")])

    def test_validate_project_returns_valid_report(self) -> None:
        engine = FarcelEngine(_Importer())

        report = engine.validate_project("project-root", SimulationProject("project", "Demo"))

        self.assertTrue(report.is_valid)

    def test_validate_project_maps_invalid_report_to_config_error(self) -> None:
        engine = FarcelEngine(_Importer())

        with self.assertRaises(EngineError) as raised:
            engine.validate_project("project-root", SimulationProject("", "Demo"))

        self.assertEqual(raised.exception.code, ErrorCode.CONFIG_ERROR)
        self.assertEqual(
            raised.exception.details["issues"],
            (
                {
                    "field": "project_id",
                    "code": "EMPTY_PROJECT_ID",
                    "message": "project_id 不能为空",
                },
            ),
        )

    def test_repository_errors_propagate_unchanged(self) -> None:
        project = SimulationProject("project", "Demo")
        repository = _ProjectRepository(project)
        load_error = EngineError(ErrorCode.PROJECT_IO_ERROR, "project.json 不存在", {"path": "project.json"})
        save_error = EngineError(ErrorCode.PROJECT_FORMAT_ERROR, "项目格式无效", {"field": "name"})
        repository.load_error = load_error
        repository.save_error = save_error
        engine = FarcelEngine(_Importer(), project_repository=repository)

        with self.assertRaises(EngineError) as opened:
            engine.open_project("project-root")
        with self.assertRaises(EngineError) as saved:
            engine.save_project("project-root", project)

        self.assertIs(opened.exception, load_error)
        self.assertIs(saved.exception, save_error)

    def test_missing_repository_is_not_implemented_but_validation_still_works(self) -> None:
        engine = FarcelEngine(_Importer(), None, None, None, None)
        project = SimulationProject("project", "Demo")

        with self.assertRaises(EngineError) as opened:
            engine.open_project("project-root")
        with self.assertRaises(EngineError) as saved:
            engine.save_project("project-root", project)

        self.assertEqual(opened.exception.code, ErrorCode.NOT_IMPLEMENTED)
        self.assertEqual(saved.exception.code, ErrorCode.NOT_IMPLEMENTED)
        self.assertTrue(engine.validate_project("project-root", project).is_valid)
