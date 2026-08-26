import ast
from dataclasses import fields
from pathlib import Path
from typing import get_type_hints
import unittest

from farcel.contracts import models


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
            models.ResultChunk,
            models.ExportReport,
        )

        annotations = []
        for contract_type in contract_types:
            hints = get_type_hints(contract_type)
            annotations.extend(str(hints[field.name]) for field in fields(contract_type))

        self.assertTrue(
            all("fmpy" not in annotation.lower() for annotation in annotations)
        )

    def test_contracts_and_application_do_not_import_fmpy_or_infrastructure(self) -> None:
        source_root = Path(__file__).parents[2] / "src" / "farcel"
        forbidden: list[str] = []
        for package in ("contracts", "application"):
            for source_file in (source_root / package).glob("*.py"):
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
                        if name == "fmpy"
                        or name.startswith("fmpy.")
                        or name.startswith("farcel.infrastructure")
                    )

        self.assertEqual(forbidden, [])
