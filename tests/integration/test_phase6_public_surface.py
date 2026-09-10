import unittest

from farcel import create_backend
from farcel.contracts import (
    PROJECT_RUN_ARTIFACT_SCHEMA_VERSION,
    PROJECT_SCHEMA_VERSION,
    GraphResultExporter,
    ProjectBatchProgress,
    ProjectBatchRunItem,
    ProjectBatchRunResult,
    ProjectRunComparison,
    ProjectRunSignalComparison,
    ProjectRunSignalSeries,
    SignalStatistics,
    SignalStatisticsStatus,
)


class Phase6PublicSurfaceTests(unittest.TestCase):
    def test_default_backend_exposes_final_phase6_workflow_methods(self) -> None:
        backend = create_backend()

        self.assertTrue(callable(backend.export_graph_result))
        self.assertTrue(callable(backend.run_project_cases))
        self.assertTrue(callable(backend.compare_project_runs))

    def test_phase6_contracts_and_schema_versions_are_public(self) -> None:
        for contract in (
            GraphResultExporter,
            ProjectBatchProgress,
            ProjectBatchRunItem,
            ProjectBatchRunResult,
            ProjectRunComparison,
            ProjectRunSignalComparison,
            ProjectRunSignalSeries,
            SignalStatistics,
            SignalStatisticsStatus,
        ):
            self.assertIsNotNone(contract)
        self.assertEqual(PROJECT_SCHEMA_VERSION, "1.0")
        self.assertEqual(PROJECT_RUN_ARTIFACT_SCHEMA_VERSION, "1.0")
