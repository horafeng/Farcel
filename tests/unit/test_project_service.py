import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from farcel.application.project_assets import ProjectAssetCheck, ProjectAssetValidator
from farcel.application.project_service import ProjectService
from farcel.application.project_validation import ProjectValidator
from farcel.contracts import (
    GraphSimulationConfig,
    GraphSimulationResult,
    InterfaceCapability,
    InterfaceType,
    ModelAsset,
    ModelMetadata,
    ModelNode,
    ProjectRunRecord,
    RunControl,
    SimulationCase,
    SimulationGraph,
    SimulationProject,
    SimulationState,
    ValidationIssue,
    ValidationReport,
)
from farcel.contracts.errors import EngineError, ErrorCode


class _Importer:
    def __init__(self, models: dict[str, ModelMetadata]) -> None:
        self.models = models

    def load(self, path: Path) -> ModelMetadata:
        return self.models[str(path)]


class _RunGraphSpy:
    def __init__(self, result: GraphSimulationResult) -> None:
        self.result = result
        self.calls: list[tuple[SimulationGraph, GraphSimulationConfig, object, object]] = []

    def __call__(self, graph, config, *, control=None, on_progress=None):
        self.calls.append((graph, config, control, on_progress))
        return self.result


class _InvalidatingAssetValidator(ProjectAssetValidator):
    def check(self, project_root: Path, asset: ModelAsset) -> ProjectAssetCheck:
        return ProjectAssetCheck(
            None,
            ValidationReport((ValidationIssue("sha256", "ASSET_CHECKSUM_MISMATCH", "asset changed"),)),
        )


class ProjectServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "Project"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_valid_case_delegates_absolute_graph_and_forwards_arguments(self) -> None:
        project, importer = self._project_with_cases(("case", "models/plant.fmu"))
        result = _result()
        runner = _RunGraphSpy(result)
        control = RunControl()
        progress = lambda _: None

        returned = ProjectService(ProjectValidator(importer), runner).run_case(
            self.root, project, "case", control=control, on_progress=progress
        )

        self.assertIs(returned, result)
        self.assertEqual(len(runner.calls), 1)
        graph, config, received_control, received_progress = runner.calls[0]
        self.assertEqual(graph.nodes[0].model_path, str((self.root / "models/plant.fmu").resolve()))
        self.assertTrue(Path(graph.nodes[0].model_path).is_absolute())
        self.assertIs(config, project.simulation_cases[0].config)
        self.assertIs(received_control, control)
        self.assertIs(received_progress, progress)
        self.assertEqual(project.simulation_cases[0].graph.nodes[0].model_path, "models/plant.fmu")

    def test_invalid_project_raises_config_error_without_execution(self) -> None:
        asset = self._asset("plant", "models/plant.fmu", b"plant")
        project = SimulationProject(
            "project", "Demo", (ModelAsset(asset.asset_id, asset.display_name, asset.relative_path, "0" * 64),),
            (self._case("case", asset.relative_path),),
        )
        runner = _RunGraphSpy(_result())
        importer = _Importer({str((self.root / asset.relative_path).resolve()): _metadata("plant")})

        with self.assertRaises(EngineError) as raised:
            ProjectService(ProjectValidator(importer), runner).run_case(self.root, project, "case")

        self.assertIs(raised.exception.code, ErrorCode.CONFIG_ERROR)
        self.assertIn("ASSET_CHECKSUM_MISMATCH", {issue["code"] for issue in raised.exception.details["issues"]})
        self.assertEqual(runner.calls, [])

    def test_unknown_case_returns_config_error_without_execution(self) -> None:
        project, importer = self._project_with_cases(("case", "models/plant.fmu"))
        runner = _RunGraphSpy(_result())

        with self.assertRaises(EngineError) as raised:
            ProjectService(ProjectValidator(importer), runner).run_case(self.root, project, "missing")

        self.assertIs(raised.exception.code, ErrorCode.CONFIG_ERROR)
        self.assertEqual(raised.exception.details["issues"][0]["code"], "UNKNOWN_CASE_ID")
        self.assertEqual(runner.calls, [])

    def test_requested_case_selects_only_its_graph_and_config(self) -> None:
        first = self._asset("first", "models/first.fmu", b"first")
        second = self._asset("second", "models/second.fmu", b"second")
        first_case = self._case("case-A", first.relative_path, GraphSimulationConfig(stop_time=0.01))
        second_config = GraphSimulationConfig(stop_time=0.02, communication_step=0.01)
        second_case = self._case("case-B", second.relative_path, second_config)
        project = SimulationProject("project", "Demo", (first, second), (first_case, second_case))
        importer = _Importer({
            str((self.root / first.relative_path).resolve()): _metadata("first"),
            str((self.root / second.relative_path).resolve()): _metadata("second"),
        })
        runner = _RunGraphSpy(_result())

        ProjectService(ProjectValidator(importer), runner).run_case(self.root, project, "case-B")

        graph, config, _, _ = runner.calls[0]
        self.assertEqual(graph.nodes[0].node_id, "case-B-node")
        self.assertEqual(graph.nodes[0].model_path, str((self.root / second.relative_path).resolve()))
        self.assertIs(config, second_config)

    def test_target_asset_recheck_failure_prevents_execution(self) -> None:
        project, importer = self._project_with_cases(("case", "models/plant.fmu"))
        runner = _RunGraphSpy(_result())
        service = ProjectService(
            ProjectValidator(importer), runner, _InvalidatingAssetValidator()
        )

        with self.assertRaises(EngineError) as raised:
            service.run_case(self.root, project, "case")

        self.assertIs(raised.exception.code, ErrorCode.CONFIG_ERROR)
        self.assertEqual(
            raised.exception.details["issues"][0],
            {"field": "model_assets[0].sha256", "code": "ASSET_CHECKSUM_MISMATCH", "message": "asset changed"},
        )
        self.assertEqual(runner.calls, [])

    def test_run_history_is_not_changed(self) -> None:
        project, importer = self._project_with_cases(("case", "models/plant.fmu"))
        history = (ProjectRunRecord("existing", "case", SimulationState.COMPLETED, 1.0, 1, "results/existing.json"),)
        project = SimulationProject(project.project_id, project.name, project.model_assets, project.simulation_cases, history)
        runner = _RunGraphSpy(_result())

        ProjectService(ProjectValidator(importer), runner).run_case(self.root, project, "case")

        self.assertEqual(project.run_history, history)

    def _project_with_cases(self, *case_specs: tuple[str, str]) -> tuple[SimulationProject, _Importer]:
        assets = []
        cases = []
        models = {}
        for case_id, path in case_specs:
            asset = self._asset(case_id, path, case_id.encode())
            assets.append(asset)
            cases.append(self._case(case_id, path))
            models[str((self.root / path).resolve())] = _metadata(case_id)
        return SimulationProject("project", "Demo", tuple(assets), tuple(cases)), _Importer(models)

    def _asset(self, asset_id: str, relative_path: str, contents: bytes) -> ModelAsset:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return ModelAsset(asset_id, asset_id, relative_path, hashlib.sha256(contents).hexdigest())

    @staticmethod
    def _case(case_id: str, model_path: str, config: GraphSimulationConfig | None = None) -> SimulationCase:
        return SimulationCase(
            case_id,
            case_id,
            SimulationGraph(nodes=(ModelNode(f"{case_id}-node", model_path),)),
            config or GraphSimulationConfig(stop_time=0.02, communication_step=0.01),
        )


def _metadata(name: str) -> ModelMetadata:
    return ModelMetadata(
        model_id=name,
        source_path=f"{name}.fmu",
        fmi_version="2.0",
        model_name=name,
        interface_types=(InterfaceType.CO_SIMULATION,),
        executable_interface=InterfaceType.CO_SIMULATION,
        interface_capabilities=(InterfaceCapability(InterfaceType.CO_SIMULATION, can_execute=True),),
    )


def _result() -> GraphSimulationResult:
    return GraphSimulationResult(
        0.0, 0.02, 0.01, 2, 0.02, SimulationState.COMPLETED,
        (0.0, 0.01, 0.02), {"node": {"x": (1.0, 1.0, 1.0)}},
    )
