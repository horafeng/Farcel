from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import get_type_hints
import unittest

from farcel.contracts import (
    PROJECT_SCHEMA_VERSION,
    GraphSimulationConfig,
    ModelAsset,
    ProjectRepository,
    ProjectRunRecord,
    SimulationCase,
    SimulationGraph,
    SimulationProject,
    SimulationState,
)


class ProjectContractTests(unittest.TestCase):
    def test_model_asset_preserves_declarative_fields(self) -> None:
        asset = ModelAsset(
            asset_id="plant",
            display_name="Plant",
            relative_path="models/plant.fmu",
            sha256="abc",
        )

        self.assertEqual(asset.asset_id, "plant")
        self.assertEqual(asset.display_name, "Plant")
        self.assertEqual(asset.relative_path, "models/plant.fmu")
        self.assertEqual(asset.sha256, "abc")

    def test_simulation_case_reuses_existing_graph_contracts(self) -> None:
        graph = SimulationGraph()
        config = GraphSimulationConfig()
        case = SimulationCase("nominal", "Nominal", graph, config)

        self.assertIs(case.graph, graph)
        self.assertIs(case.config, config)

    def test_simulation_project_defaults(self) -> None:
        project = SimulationProject(project_id="project-1", name="Demo")

        self.assertEqual(project.schema_version, PROJECT_SCHEMA_VERSION)
        self.assertEqual(project.model_assets, ())
        self.assertEqual(project.simulation_cases, ())
        self.assertEqual(project.run_history, ())

    def test_nested_project_structure_is_representable(self) -> None:
        asset = ModelAsset("plant", "Plant", "models/plant.fmu", "abc")
        case = SimulationCase(
            "nominal", "Nominal", SimulationGraph(), GraphSimulationConfig()
        )
        record = ProjectRunRecord(
            run_id="run-1",
            case_id="nominal",
            completion_state=SimulationState.COMPLETED,
            final_time=1.0,
            completed_steps=100,
            result_path="results/run-1.json",
        )
        project = SimulationProject(
            project_id="project-1",
            name="Demo",
            model_assets=(asset,),
            simulation_cases=(case,),
            run_history=(record,),
        )

        self.assertEqual(project.model_assets, (asset,))
        self.assertEqual(project.simulation_cases, (case,))
        self.assertEqual(project.run_history, (record,))

    def test_project_contracts_are_frozen(self) -> None:
        asset = ModelAsset("plant", "Plant", "models/plant.fmu", "abc")
        case = SimulationCase(
            "nominal", "Nominal", SimulationGraph(), GraphSimulationConfig()
        )
        record = ProjectRunRecord(
            "run-1",
            "nominal",
            SimulationState.COMPLETED,
            1.0,
            1,
            "results/run-1.json",
        )
        project = SimulationProject("project-1", "Demo")

        for contract, field_name in (
            (asset, "asset_id"),
            (case, "case_id"),
            (record, "run_id"),
            (project, "project_id"),
        ):
            with self.subTest(contract=type(contract).__name__):
                with self.assertRaises(FrozenInstanceError):
                    setattr(contract, field_name, "changed")

    def test_semantically_invalid_project_state_remains_declarative(self) -> None:
        project = SimulationProject(
            project_id="p",
            name="x",
            model_assets=(
                ModelAsset("same", "A", "../bad.fmu", "bad"),
                ModelAsset("same", "B", "C:/absolute.fmu", "also-bad"),
            ),
            schema_version="999.0",
        )

        self.assertEqual(project.schema_version, "999.0")
        self.assertEqual(
            tuple(asset.asset_id for asset in project.model_assets), ("same", "same")
        )

    def test_repository_port_is_public_and_uses_runtime_path_context(self) -> None:
        self.assertIsNotNone(ProjectRepository)
        self.assertEqual(
            get_type_hints(ProjectRepository.load),
            {"project_root": Path, "return": SimulationProject},
        )
        self.assertEqual(
            get_type_hints(ProjectRepository.save),
            {
                "project_root": Path,
                "project": SimulationProject,
                "return": type(None),
            },
        )

    def test_project_fields_have_no_runtime_root(self) -> None:
        self.assertEqual(
            tuple(field.name for field in fields(SimulationProject)),
            (
                "project_id",
                "name",
                "model_assets",
                "simulation_cases",
                "run_history",
                "schema_version",
            ),
        )
