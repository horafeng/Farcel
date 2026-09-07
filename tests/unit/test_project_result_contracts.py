from dataclasses import FrozenInstanceError, fields
import unittest

from farcel.contracts import (
    PROJECT_RUN_ARTIFACT_SCHEMA_VERSION,
    GraphSimulationConfig,
    GraphSimulationResult,
    ProjectAssetSnapshot,
    ProjectRunArtifact,
    SimulationCase,
    SimulationGraph,
    SimulationState,
)


class ProjectResultContractTests(unittest.TestCase):
    def test_artifact_schema_is_independent_and_defaulted(self) -> None:
        artifact = ProjectRunArtifact(
            "run", "project", SimulationCase("case", "Case", SimulationGraph(), GraphSimulationConfig()), (), _result()
        )

        self.assertEqual(PROJECT_RUN_ARTIFACT_SCHEMA_VERSION, "1.0")
        self.assertEqual(artifact.schema_version, PROJECT_RUN_ARTIFACT_SCHEMA_VERSION)

    def test_contract_dtos_are_frozen_and_slots_based(self) -> None:
        snapshot = ProjectAssetSnapshot("asset", "models/plant.fmu", "a" * 64)

        self.assertEqual([field.name for field in fields(ProjectAssetSnapshot)], ["asset_id", "relative_path", "sha256"])
        self.assertEqual([field.name for field in fields(ProjectRunArtifact)], ["run_id", "project_id", "case_snapshot", "asset_snapshots", "result", "schema_version"])
        self.assertTrue(hasattr(ProjectAssetSnapshot, "__slots__"))
        self.assertTrue(hasattr(ProjectRunArtifact, "__slots__"))
        with self.assertRaises(FrozenInstanceError):
            snapshot.sha256 = "b" * 64


def _result() -> GraphSimulationResult:
    return GraphSimulationResult(0.0, 0.01, 0.01, 1, 0.01, SimulationState.COMPLETED, (0.0, 0.01), {})
