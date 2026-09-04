import ast
from dataclasses import fields
from pathlib import Path
from typing import get_type_hints
import unittest

from farcel.contracts import graph, models


class ContractBoundaryTests(unittest.TestCase):
    def test_public_contract_annotations_do_not_reference_fmpy(self) -> None:
        contract_types = (
            models.DefaultExperiment,
            models.CapabilitySet,
            models.InterfaceCapability,
            models.VariableMetadata,
            models.ModelMetadata,
            models.SimulationConfig,
            models.ValidationIssue,
            models.ValidationReport,
            models.SessionHandle,
            models.StepResult,
            models.ModelExchangeInitialization,
            models.IntegratorStepResult,
            models.DiscreteStateUpdate,
            models.SolverAdvanceResult,
            models.SolverOptions,
            models.ResultChunk,
            models.ExportReport,
            models.RunSummary,
            models.SimulationResult,
            graph.PortReference,
            graph.Connection,
            graph.ModelNodeConfig,
            graph.ModelNode,
            graph.SimulationGraph,
            graph.GraphSimulationConfig,
            graph.GraphSimulationResult,
        )

        annotations = []
        for contract_type in contract_types:
            hints = get_type_hints(contract_type)
            annotations.extend(str(hints[field.name]) for field in fields(contract_type))

        self.assertTrue(
            all("fmpy" not in annotation.lower() for annotation in annotations)
        )

    def test_graph_result_annotations_are_public_contract_types_only(self) -> None:
        forbidden = (
            "fmpy", "numpy", "ctypes", "pyside", "pyqt", "infrastructure",
        )
        annotations = [str(annotation).lower()
            for annotation in get_type_hints(graph.GraphSimulationResult).values()]
        self.assertTrue(all(
            forbidden_name not in annotation
            for annotation in annotations
            for forbidden_name in forbidden
        ))

    def test_contracts_and_application_do_not_import_native_or_gui_dependencies(self) -> None:
        source_root = Path(__file__).parents[2] / "src" / "farcel"
        forbidden: list[str] = []
        forbidden_prefixes = (
            "fmpy",
            "farcel.infrastructure",
            "numpy",
            "ctypes",
            "PyQt",
            "PySide",
        )
        for package in ("contracts", "application"):
            for source_file in (source_root / package).rglob("*.py"):
                tree = ast.parse(source_file.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names = [node.module or ""]
                    else:
                        continue
                    forbidden.extend(
                        name
                        for name in names
                        if any(
                            name == prefix or name.startswith(f"{prefix}.")
                            for prefix in forbidden_prefixes
                        )
                    )

        self.assertEqual(forbidden, [])
