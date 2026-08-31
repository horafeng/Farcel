import tempfile
import unittest
import zipfile
from pathlib import Path

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts._arrays import ArrayShapeError
from farcel.infrastructure.fmpy import FmpyImporter
from farcel.infrastructure.fmpy.importer import _typed_value


class FmpyImporterErrorTests(unittest.TestCase):
    def test_fmi3_array_start_single_value_broadcasts_to_resolved_shape(self) -> None:
        self.assertEqual(
            _typed_value("2", "Float64", (3,)),
            (2.0, 2.0, 2.0),
        )
        self.assertEqual(
            _typed_value("1", "Float64", (2, 2)),
            ((1.0, 1.0), (1.0, 1.0)),
        )

    def test_fmi3_array_start_full_value_list_keeps_its_resolved_shape(self) -> None:
        self.assertEqual(_typed_value("1 2", "Float64", (2,)), (1.0, 2.0))

    def test_fmi3_array_start_wrong_value_count_is_rejected(self) -> None:
        with self.assertRaises(ArrayShapeError):
            _typed_value("1 2", "Float64", (3,))

    def test_rejects_missing_file_with_stable_error(self) -> None:
        with self.assertRaises(EngineError) as raised:
            FmpyImporter().load(Path("missing.fmu"))
        self.assertEqual(raised.exception.code, ErrorCode.IMPORT_ERROR)

    def test_reports_fmi_validation_failure_without_leaking_exception(self) -> None:
        invalid_xml = """<?xml version="1.0"?>
<fmiModelDescription fmiVersion="2.0" modelName="Broken" guid="broken"/>
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.fmu"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("modelDescription.xml", invalid_xml)
            with self.assertRaises(EngineError) as raised:
                FmpyImporter().load(path)

        self.assertEqual(raised.exception.code, ErrorCode.VALIDATION_ERROR)
        self.assertIn("diagnostics", raised.exception.details)
