import unittest

from farcel.application.project_results import build_project_run_artifact
from farcel.contracts import (
    Connection,
    EngineError,
    ErrorCode,
    GraphSimulationConfig,
    GraphSimulationResult,
    InputUpdate,
    ModelAsset,
    ModelNode,
    ModelNodeConfig,
    PortReference,
    SimulationCase,
    SimulationGraph,
    SimulationProject,
    SimulationState,
)


class ProjectResultBuilderTests(unittest.TestCase):
    def test_selects_exact_case_and_used_assets_in_node_order(self) -> None:
        project = _project()

        artifact = build_project_run_artifact(project, "case-B", "run-1", _result())

        self.assertEqual((artifact.run_id, artifact.project_id), ("run-1", "project"))
        self.assertEqual(artifact.case_snapshot.case_id, "case-B")
        self.assertEqual(artifact.case_snapshot.graph.nodes[0].model_path, "models/controller.fmu")
        self.assertEqual(
            tuple(snapshot.relative_path for snapshot in artifact.asset_snapshots),
            ("models/controller.fmu", "models/plant.fmu"),
        )
        self.assertNotIn("models/unused.fmu", {item.relative_path for item in artifact.asset_snapshots})

    def test_repeated_asset_is_snapshotted_once(self) -> None:
        project = _project()
        shared_case = SimulationCase(
            "shared", "Shared", SimulationGraph(nodes=(ModelNode("a", "models/plant.fmu"), ModelNode("b", "models/plant.fmu"))), GraphSimulationConfig(stop_time=0.02, communication_step=0.01)
        )
        project = SimulationProject(project.project_id, project.name, project.model_assets, (shared_case,))

        artifact = build_project_run_artifact(project, "shared", "run", _result())

        self.assertEqual(tuple(item.relative_path for item in artifact.asset_snapshots), ("models/plant.fmu",))

    def test_snapshot_is_independent_of_mutable_case_and_result_mappings(self) -> None:
        parameters = {"gain": 1.0}
        initial_inputs = {"u": 2.0}
        scheduled_values = {"u": 3.0}
        outputs = {"node": {"y": [1.0, 2.0, 3.0]}}
        case = SimulationCase(
            "case", "Case", SimulationGraph(nodes=(ModelNode("node", "models/plant.fmu", ModelNodeConfig(parameters=parameters, initial_inputs=initial_inputs, input_schedule=(InputUpdate(0.01, scheduled_values),))),)), GraphSimulationConfig(stop_time=0.02, communication_step=0.01)
        )
        project = SimulationProject("project", "Demo", (ModelAsset("plant", "Plant", "models/plant.fmu", "a" * 64),), (case,))
        result = GraphSimulationResult(0.0, 0.02, 0.01, 2, 0.02, SimulationState.COMPLETED, (0.0, 0.01, 0.02), outputs)

        artifact = build_project_run_artifact(project, "case", "run", result)
        parameters["gain"] = 9.0
        initial_inputs["u"] = 9.0
        scheduled_values["u"] = 9.0
        outputs["node"]["y"][0] = 9.0

        config = artifact.case_snapshot.graph.nodes[0].config
        self.assertEqual(config.parameters["gain"], 1.0)
        self.assertEqual(config.initial_inputs["u"], 2.0)
        self.assertEqual(config.input_schedule[0].values["u"], 3.0)
        self.assertEqual(artifact.result.node_outputs["node"]["y"][0], 1.0)

    def test_blank_run_id_unknown_case_and_bad_asset_binding_are_config_errors(self) -> None:
        project = _project()
        cases = (
            ("case-A", "", "EMPTY_RUN_ID"),
            ("missing", "run", "UNKNOWN_CASE_ID"),
        )
        for case_id, run_id, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(EngineError) as raised:
                    build_project_run_artifact(project, case_id, run_id, _result())
                self.assertIs(raised.exception.code, ErrorCode.CONFIG_ERROR)
                self.assertEqual(raised.exception.details["issues"][0]["code"], code)

        unregistered = SimulationProject("project", "Demo", (), (SimulationCase("case", "Case", SimulationGraph(nodes=(ModelNode("node", "missing.fmu"),)), GraphSimulationConfig()),))
        duplicate_assets = (ModelAsset("one", "One", "models/plant.fmu", "a" * 64), ModelAsset("two", "Two", "models/plant.fmu", "b" * 64))
        ambiguous = SimulationProject("project", "Demo", duplicate_assets, (SimulationCase("case", "Case", SimulationGraph(nodes=(ModelNode("node", "models/plant.fmu"),)), GraphSimulationConfig()),))
        for inconsistent, code in ((unregistered, "UNREGISTERED_MODEL_ASSET"), (ambiguous, "AMBIGUOUS_MODEL_ASSET")):
            with self.subTest(code=code):
                with self.assertRaises(EngineError) as raised:
                    build_project_run_artifact(inconsistent, "case", "run", _result())
                self.assertEqual(raised.exception.details["issues"][0]["code"], code)


def _project() -> SimulationProject:
    assets = (
        ModelAsset("plant", "Plant", "models/plant.fmu", "a" * 64),
        ModelAsset("controller", "Controller", "models/controller.fmu", "b" * 64),
        ModelAsset("unused", "Unused", "models/unused.fmu", "c" * 64),
    )
    case_a = SimulationCase("case-A", "A", SimulationGraph(nodes=(ModelNode("plant", "models/plant.fmu"),)), GraphSimulationConfig(stop_time=0.02, communication_step=0.01))
    case_b = SimulationCase(
        "case-B", "B", SimulationGraph(nodes=(ModelNode("controller", "models/controller.fmu"), ModelNode("plant", "models/plant.fmu")), connections=(Connection(PortReference("controller", "y"), PortReference("plant", "u")),)), GraphSimulationConfig(stop_time=0.02, communication_step=0.01)
    )
    return SimulationProject("project", "Demo", assets, (case_a, case_b))


def _result() -> GraphSimulationResult:
    return GraphSimulationResult(0.0, 0.02, 0.01, 2, 0.02, SimulationState.COMPLETED, (0.0, 0.01, 0.02), {})
