import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from farcel.application.project_validation import ProjectValidator
from farcel.contracts import (
    GraphSimulationConfig,
    InterfaceCapability,
    InterfaceType,
    ModelAsset,
    ModelMetadata,
    ModelNode,
    ProjectRunRecord,
    SimulationCase,
    SimulationGraph,
    SimulationProject,
    SimulationState,
)
from farcel.contracts.errors import EngineError, ErrorCode


class _Importer:
    def __init__(self, outcomes: dict[str, ModelMetadata | BaseException]) -> None:
        self.outcomes = outcomes
        self.loaded: list[str] = []

    def load(self, path: Path) -> ModelMetadata:
        path_string = str(path)
        self.loaded.append(path_string)
        outcome = self.outcomes[path_string]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class ProjectValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "Project"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_valid_case_materializes_absolute_graph_without_mutating_project(self) -> None:
        asset = self._asset("plant", "models/plant.fmu", b"plant")
        project = self._project(assets=(asset,), cases=(self._case("nominal", "models/plant.fmu"),))
        absolute_path = str((self.root / asset.relative_path).resolve())
        importer = _Importer({absolute_path: _metadata("plant")})

        report = ProjectValidator(importer).validate(self.root, project)

        self.assertTrue(report.is_valid)
        self.assertEqual(importer.loaded, [absolute_path])
        self.assertEqual(project.simulation_cases[0].graph.nodes[0].model_path, "models/plant.fmu")

    def test_project_and_case_identity_issues_are_reported(self) -> None:
        project = SimulationProject(
            " ", "Demo", simulation_cases=(self._case(" ", "missing.fmu"), self._case("same", "missing.fmu"), self._case("same", "missing.fmu")), schema_version="2.0"
        )

        report = ProjectValidator(_Importer({})).validate(self.root, project)

        self.assertEqual(
            _codes(report),
            ("UNSUPPORTED_PROJECT_SCHEMA", "EMPTY_PROJECT_ID", "EMPTY_CASE_ID", "DUPLICATE_CASE_ID", "UNREGISTERED_MODEL_ASSET", "UNREGISTERED_MODEL_ASSET", "UNREGISTERED_MODEL_ASSET"),
        )

    def test_asset_identity_and_duplicate_relative_path_are_reported_without_import(self) -> None:
        first = self._asset("same", "models/plant.fmu", b"plant")
        second = self._asset("same", "models/plant.fmu", b"plant")
        project = self._project(assets=(first, second), cases=(self._case("case", "models/plant.fmu"),))
        importer = _Importer({})

        report = ProjectValidator(importer).validate(self.root, project)

        self.assertEqual(importer.loaded, [])
        self.assertEqual(
            _field_codes(report),
            (
                ("model_assets[1].asset_id", "DUPLICATE_ASSET_ID"),
                ("model_assets[1].relative_path", "DUPLICATE_ASSET_PATH"),
                ("simulation_cases[0].graph.nodes[0].model_path", "AMBIGUOUS_MODEL_ASSET"),
            ),
        )

    def test_blank_asset_id_is_reported(self) -> None:
        asset = self._asset("asset", "models/plant.fmu", b"plant")
        project = self._project(
            assets=(ModelAsset(" ", asset.display_name, asset.relative_path, asset.sha256),)
        )

        report = ProjectValidator(_Importer({})).validate(self.root, project)

        self.assertEqual(_field_codes(report), (("model_assets[0].asset_id", "EMPTY_ASSET_ID"),))

    def test_asset_integrity_issues_aggregate_and_block_bound_node_import(self) -> None:
        valid = self._asset("valid", "models/valid.fmu", b"valid")
        invalid = self._asset("bad", "models/bad.fmu", b"actual contents")
        invalid = ModelAsset("bad", "Bad", invalid.relative_path, "0" * 64)
        project = self._project(
            assets=(valid, invalid), cases=(self._case("case", invalid.relative_path),)
        )
        importer = _Importer({str((self.root / valid.relative_path).resolve()): _metadata("valid")})

        report = ProjectValidator(importer).validate(self.root, project)

        self.assertEqual(importer.loaded, [])
        self.assertEqual(
            _field_codes(report),
            (
                ("model_assets[1].sha256", "ASSET_CHECKSUM_MISMATCH"),
                ("simulation_cases[0].graph.nodes[0].model_path", "MODEL_ASSET_INVALID"),
            ),
        )

    def test_unregistered_node_path_does_not_import(self) -> None:
        asset = self._asset("plant", "models/plant.fmu", b"plant")
        importer = _Importer({str((self.root / asset.relative_path).resolve()): _metadata("plant")})
        project = self._project(assets=(asset,), cases=(self._case("case", "models/other.fmu"),))

        report = ProjectValidator(importer).validate(self.root, project)

        self.assertEqual(importer.loaded, [])
        self.assertEqual(_codes(report), ("UNREGISTERED_MODEL_ASSET",))

    def test_run_history_identity_and_case_reference_are_checked(self) -> None:
        records = (
            _record("run", "case"),
            _record("run", "unknown"),
            _record(" ", "unknown"),
        )
        project = self._project(cases=(self._case("case", "missing.fmu"),), records=records)

        report = ProjectValidator(_Importer({})).validate(self.root, project)

        self.assertEqual(
            _field_codes(report)[:4],
            (
                ("run_history[1].run_id", "DUPLICATE_RUN_ID"),
                ("run_history[1].case_id", "UNKNOWN_RUN_CASE"),
                ("run_history[2].run_id", "EMPTY_RUN_ID"),
                ("run_history[2].case_id", "UNKNOWN_RUN_CASE"),
            ),
        )

    def test_graph_node_issues_are_prefixed_with_case_graph_path(self) -> None:
        asset = self._asset("plant", "models/plant.fmu", b"plant")
        graph = SimulationGraph(nodes=(ModelNode("same", asset.relative_path), ModelNode("same", asset.relative_path)))
        project = self._project(assets=(asset,), cases=(SimulationCase("case", "Case", graph, GraphSimulationConfig()),))
        absolute_path = str((self.root / asset.relative_path).resolve())
        importer = _Importer({absolute_path: _metadata("plant")})

        report = ProjectValidator(importer).validate(self.root, project)

        self.assertEqual(
            _field_codes(report),
            (("simulation_cases[0].graph.nodes[1].node_id", "DUPLICATE_NODE_ID"),),
        )
        self.assertEqual(importer.loaded, [absolute_path, absolute_path])

    def test_graph_config_issues_are_prefixed_with_case_config_path(self) -> None:
        asset = self._asset("plant", "models/plant.fmu", b"plant")
        project = self._project(
            assets=(asset,),
            cases=(self._case("case", asset.relative_path, GraphSimulationConfig(stop_time=1.0, communication_step=0.3)),),
        )
        absolute_path = str((self.root / asset.relative_path).resolve())

        report = ProjectValidator(_Importer({absolute_path: _metadata("plant")})).validate(self.root, project)

        self.assertEqual(
            _field_codes(report),
            (("simulation_cases[0].config.communication_step", "GRAPH_DURATION_NOT_COMMUNICATION_ALIGNED"),),
        )

    def test_import_error_is_prefixed_with_case_graph_path(self) -> None:
        asset = self._asset("plant", "models/plant.fmu", b"plant")
        project = self._project(assets=(asset,), cases=(self._case("case", asset.relative_path),))
        absolute_path = str((self.root / asset.relative_path).resolve())

        report = ProjectValidator(_Importer({absolute_path: EngineError(ErrorCode.IMPORT_ERROR, "bad FMU")})).validate(self.root, project)

        self.assertEqual(
            _field_codes(report),
            (("simulation_cases[0].graph.nodes[0].model_path", "IMPORT_ERROR"),),
        )

    def test_one_registered_asset_can_be_used_by_multiple_nodes(self) -> None:
        asset = self._asset("shared", "models/shared.fmu", b"shared")
        graph = SimulationGraph(
            nodes=(
                ModelNode("first", asset.relative_path),
                ModelNode("second", asset.relative_path),
            )
        )
        project = self._project(
            assets=(asset,),
            cases=(SimulationCase("case", "Case", graph, GraphSimulationConfig()),),
        )
        absolute_path = str((self.root / asset.relative_path).resolve())
        importer = _Importer({absolute_path: _metadata("shared")})

        report = ProjectValidator(importer).validate(self.root, project)

        self.assertTrue(report.is_valid)
        self.assertEqual(importer.loaded, [absolute_path, absolute_path])

    def test_unused_invalid_asset_does_not_block_a_valid_case(self) -> None:
        used = self._asset("used", "models/used.fmu", b"used")
        unused = ModelAsset("unused", "Unused", "models/missing.fmu", "0" * 64)
        project = self._project(assets=(used, unused), cases=(self._case("case", used.relative_path),))
        absolute_path = str((self.root / used.relative_path).resolve())
        importer = _Importer({absolute_path: _metadata("used")})

        report = ProjectValidator(importer).validate(self.root, project)

        self.assertEqual(importer.loaded, [absolute_path])
        self.assertEqual(_codes(report), ("ASSET_MISSING",))

    def _asset(self, asset_id: str, relative_path: str, contents: bytes) -> ModelAsset:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return ModelAsset(asset_id, asset_id, relative_path, hashlib.sha256(contents).hexdigest())

    @staticmethod
    def _case(case_id: str, model_path: str, config: GraphSimulationConfig | None = None) -> SimulationCase:
        return SimulationCase(case_id, case_id, SimulationGraph(nodes=(ModelNode("node", model_path),)), config or GraphSimulationConfig())

    @staticmethod
    def _project(*, assets=(), cases=(), records=()) -> SimulationProject:
        return SimulationProject("project", "Demo", assets, cases, records)


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


def _record(run_id: str, case_id: str) -> ProjectRunRecord:
    return ProjectRunRecord(run_id, case_id, SimulationState.COMPLETED, 1.0, 1, "results/run.json")


def _codes(report) -> tuple[str, ...]:
    return tuple(issue.code for issue in report.issues)


def _field_codes(report) -> tuple[tuple[str, str], ...]:
    return tuple((issue.field, issue.code) for issue in report.issues)
