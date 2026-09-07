import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from farcel.contracts import (
    Connection,
    EngineError,
    ErrorCode,
    GraphSimulationConfig,
    InputUpdate,
    InterfaceType,
    ModelAsset,
    ModelNode,
    ModelNodeConfig,
    PortReference,
    ProjectRunRecord,
    SimulationCase,
    SimulationGraph,
    SimulationProject,
    SimulationState,
)
from farcel.infrastructure.project import LocalJsonProjectRepository


class LocalJsonProjectRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = LocalJsonProjectRepository()
        self.temporary_directory = TemporaryDirectory()
        self.project_root = Path(self.temporary_directory.name) / "Demo"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_empty_project_round_trips(self) -> None:
        project = SimulationProject(project_id="p1", name="Demo")

        self.repository.save(self.project_root, project)

        self.assertEqual(self.repository.load(self.project_root), project)

    def test_complete_project_round_trips_existing_graph_contracts(self) -> None:
        project = _complete_project()

        self.repository.save(self.project_root, project)

        loaded = self.repository.load(self.project_root)
        self.assertEqual(loaded, project)
        config = loaded.simulation_cases[0].graph.nodes[0].config
        self.assertIsInstance(config.parameters["A"], tuple)
        self.assertEqual(config.parameters["A"], (1.0, 2.0))

    def test_model_path_is_preserved_as_the_persisted_relative_path(self) -> None:
        self.repository.save(self.project_root, _complete_project())

        loaded = self.repository.load(self.project_root)

        self.assertEqual(
            loaded.simulation_cases[0].graph.nodes[1].model_path,
            "models/plant.fmu",
        )

    def test_utf8_project_and_asset_names_round_trip(self) -> None:
        project = SimulationProject(
            project_id="p1",
            name="控制系统项目",
            model_assets=(ModelAsset("plant", "被控对象", "models/plant.fmu", "abc"),),
        )

        self.repository.save(self.project_root, project)

        project_json = (self.project_root / "project.json").read_text(encoding="utf-8")
        self.assertIn("控制系统项目", project_json)
        self.assertIn("被控对象", project_json)
        self.assertEqual(self.repository.load(self.project_root), project)

    def test_saved_document_has_canonical_top_level_shape(self) -> None:
        self.repository.save(self.project_root, SimulationProject("p1", "Demo"))

        document = json.loads((self.project_root / "project.json").read_text(encoding="utf-8"))

        self.assertEqual(
            tuple(document),
            (
                "schema_version",
                "project_id",
                "name",
                "model_assets",
                "simulation_cases",
                "run_history",
            ),
        )
        self.assertEqual(document["schema_version"], "1.0")

    def test_unknown_schema_is_a_format_error_on_load(self) -> None:
        self.project_root.mkdir()
        (self.project_root / "project.json").write_text(
            json.dumps(_empty_document(schema_version="999.0")), encoding="utf-8"
        )

        with self.assertRaises(EngineError) as captured:
            self.repository.load(self.project_root)

        self.assertIs(captured.exception.code, ErrorCode.PROJECT_FORMAT_ERROR)

    def test_unknown_schema_is_a_format_error_on_save_without_creating_document(self) -> None:
        project = SimulationProject("p1", "Demo", schema_version="999.0")

        with self.assertRaises(EngineError) as captured:
            self.repository.save(self.project_root, project)

        self.assertIs(captured.exception.code, ErrorCode.PROJECT_FORMAT_ERROR)
        self.assertFalse((self.project_root / "project.json").exists())

    def test_malformed_json_is_a_format_error(self) -> None:
        self.project_root.mkdir()
        (self.project_root / "project.json").write_text(
            "{ definitely not json", encoding="utf-8"
        )

        with self.assertRaises(EngineError) as captured:
            self.repository.load(self.project_root)

        self.assertIs(captured.exception.code, ErrorCode.PROJECT_FORMAT_ERROR)

    def test_invalid_utf8_is_a_format_error(self) -> None:
        self.project_root.mkdir()
        (self.project_root / "project.json").write_bytes(b"\xff\xfe")

        with self.assertRaises(EngineError) as captured:
            self.repository.load(self.project_root)

        self.assertIs(captured.exception.code, ErrorCode.PROJECT_FORMAT_ERROR)

    def test_missing_project_json_is_an_io_error(self) -> None:
        self.project_root.mkdir()

        with self.assertRaises(EngineError) as captured:
            self.repository.load(self.project_root)

        self.assertIs(captured.exception.code, ErrorCode.PROJECT_IO_ERROR)

    def test_invalid_interface_type_is_a_format_error(self) -> None:
        self.repository.save(self.project_root, _complete_project())
        document = _read_document(self.project_root)
        document["simulation_cases"][0]["graph"]["nodes"][0]["config"][
            "execution_interface"
        ] = "magic_solver"
        _write_document(self.project_root, document)

        with self.assertRaises(EngineError) as captured:
            self.repository.load(self.project_root)

        self.assertIs(captured.exception.code, ErrorCode.PROJECT_FORMAT_ERROR)

    def test_invalid_simulation_state_is_a_format_error(self) -> None:
        self.repository.save(self.project_root, _complete_project())
        document = _read_document(self.project_root)
        document["run_history"][0]["completion_state"] = "teleported"
        _write_document(self.project_root, document)

        with self.assertRaises(EngineError) as captured:
            self.repository.load(self.project_root)

        self.assertIs(captured.exception.code, ErrorCode.PROJECT_FORMAT_ERROR)

    def test_semantically_invalid_but_structural_project_round_trips(self) -> None:
        project = SimulationProject(
            project_id="p1",
            name="Demo",
            model_assets=(
                ModelAsset("same", "A", "../bad.fmu", "bad"),
                ModelAsset("same", "B", "C:/absolute.fmu", "also-bad"),
            ),
        )

        self.repository.save(self.project_root, project)

        self.assertEqual(self.repository.load(self.project_root), project)

    def test_normal_replacement_loads_the_new_project(self) -> None:
        first = SimulationProject("first", "First")
        second = SimulationProject("second", "Second")
        self.repository.save(self.project_root, first)

        self.repository.save(self.project_root, second)

        self.assertEqual(self.repository.load(self.project_root), second)

    def test_replace_failure_preserves_existing_document_and_cleans_temp_file(self) -> None:
        first = SimulationProject("first", "First")
        second = SimulationProject("second", "Second")
        self.repository.save(self.project_root, first)
        project_json = self.project_root / "project.json"
        original_bytes = project_json.read_bytes()

        with patch(
            "farcel.infrastructure.project.json_repository.os.replace",
            side_effect=OSError("replace blocked"),
        ):
            with self.assertRaises(EngineError) as captured:
                self.repository.save(self.project_root, second)

        self.assertIs(captured.exception.code, ErrorCode.PROJECT_IO_ERROR)
        self.assertEqual(project_json.read_bytes(), original_bytes)
        self.assertEqual(tuple(self.project_root.glob(".project-*.tmp")), ())

    def test_generic_values_round_trip_with_json_arrays_as_tuples(self) -> None:
        node = ModelNode(
            "node",
            "models/node.fmu",
            ModelNodeConfig(
                parameters={
                    "bool": True,
                    "int": 3,
                    "float": 1.5,
                    "str": "text",
                    "none": None,
                    "nested": (1, (False, "x"), {"inner": (2.0, None)}),
                }
            ),
        )
        project = SimulationProject(
            "p1",
            "Demo",
            simulation_cases=(
                SimulationCase("case", "Case", SimulationGraph(nodes=(node,)), GraphSimulationConfig()),
            ),
        )

        self.repository.save(self.project_root, project)

        self.assertEqual(self.repository.load(self.project_root), project)

    def test_unsupported_generic_value_is_a_format_error(self) -> None:
        project = _project_with_parameters({"bad": object()})

        with self.assertRaises(EngineError) as captured:
            self.repository.save(self.project_root, project)

        self.assertIs(captured.exception.code, ErrorCode.PROJECT_FORMAT_ERROR)

    def test_non_finite_generic_value_is_a_format_error_without_non_standard_json(self) -> None:
        project = _project_with_parameters({"bad": float("nan")})

        with self.assertRaises(EngineError) as captured:
            self.repository.save(self.project_root, project)

        self.assertIs(captured.exception.code, ErrorCode.PROJECT_FORMAT_ERROR)
        project_json = self.project_root / "project.json"
        self.assertFalse(project_json.exists())


def _complete_project() -> SimulationProject:
    controller = ModelNode(
        node_id="controller",
        model_path="models/controller.fmu",
        config=ModelNodeConfig(
            parameters={"gain": 2, "A": (1.0, 2.0), "nested": {"enabled": True}},
            initial_inputs={"bias": 0.5},
            input_schedule=(InputUpdate(time=0.5, values={"u": (1.0, 2.0)}),),
            selected_outputs=("y", "x"),
            relative_tolerance=1e-6,
            execution_interface=InterfaceType.MODEL_EXCHANGE,
        ),
    )
    plant = ModelNode(node_id="plant", model_path="models/plant.fmu")
    graph = SimulationGraph(
        nodes=(controller, plant),
        connections=(
            Connection(
                source=PortReference("controller", "y"),
                target=PortReference("plant", "u"),
            ),
        ),
    )
    return SimulationProject(
        project_id="project-1",
        name="Demo",
        model_assets=(
            ModelAsset("controller", "Controller", "models/controller.fmu", "controller-hash"),
            ModelAsset("plant", "Plant", "models/plant.fmu", "plant-hash"),
        ),
        simulation_cases=(
            SimulationCase(
                case_id="case-1",
                name="Nominal",
                graph=graph,
                config=GraphSimulationConfig(
                    schema_version="1.0",
                    start_time=0.0,
                    stop_time=2.0,
                    communication_step=0.1,
                    output_interval=0.2,
                ),
            ),
        ),
        run_history=(
            ProjectRunRecord(
                run_id="run-1",
                case_id="case-1",
                completion_state=SimulationState.COMPLETED,
                final_time=2.0,
                completed_steps=20,
                result_path="results/run-1.json",
            ),
        ),
    )


def _project_with_parameters(parameters: dict[str, object]) -> SimulationProject:
    node = ModelNode("node", "models/node.fmu", ModelNodeConfig(parameters=parameters))
    return SimulationProject(
        "p1",
        "Demo",
        simulation_cases=(
            SimulationCase("case", "Case", SimulationGraph(nodes=(node,)), GraphSimulationConfig()),
        ),
    )


def _empty_document(schema_version: str = "1.0") -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "project_id": "p1",
        "name": "Demo",
        "model_assets": [],
        "simulation_cases": [],
        "run_history": [],
    }


def _read_document(project_root: Path) -> dict[str, object]:
    return json.loads((project_root / "project.json").read_text(encoding="utf-8"))


def _write_document(project_root: Path, document: dict[str, object]) -> None:
    (project_root / "project.json").write_text(
        json.dumps(document), encoding="utf-8"
    )
