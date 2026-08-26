import tempfile
import unittest
import zipfile
from pathlib import Path

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.infrastructure.fmpy import FmpyImporter


class FmpyImporterErrorTests(unittest.TestCase):
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

