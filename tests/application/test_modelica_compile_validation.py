from __future__ import annotations

import math
from pathlib import Path
import tempfile
import unittest

from farcel.application.modelica_validation import (
    require_valid_modelica_compile_request,
    validate_modelica_compile_request,
)
from farcel.contracts import EngineError, ErrorCode, ModelicaCompileRequest


class ModelicaCompileValidationTests(unittest.TestCase):
    def test_valid_request_accepts_qualified_name_and_optional_compiler_path(self) -> None:
        with self._valid_paths() as (source, output):
            request = ModelicaCompileRequest(
                source,
                "ExampleSystems.MotorController",
                output,
                compiler_path=Path("compiler") / "omc.exe",
            )

            report = validate_modelica_compile_request(request)

        self.assertTrue(report.is_valid)

    def test_valid_request_allows_omitted_compiler_path(self) -> None:
        with self._valid_paths() as (source, output):
            report = validate_modelica_compile_request(
                ModelicaCompileRequest(source, "SingleModel", output)
            )

        self.assertTrue(report.is_valid)

    def test_empty_and_invalid_model_names_have_stable_issues(self) -> None:
        with self._valid_paths() as (source, output):
            for model_name, code in (
                ("", "EMPTY_MODEL_NAME"),
                ("Example.System; quit()", "INVALID_MODEL_NAME"),
                ("Example..System", "INVALID_MODEL_NAME"),
                ("Example\nSystem", "INVALID_MODEL_NAME"),
            ):
                with self.subTest(model_name=model_name):
                    report = validate_modelica_compile_request(
                        ModelicaCompileRequest(source, model_name, output)
                    )
                    self.assertEqual((("model_name", code),), self._issue_keys(report))

    def test_source_file_rules_do_not_create_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            wrong_extension = root / "source.txt"
            wrong_extension.write_text("model Source end Source;", encoding="utf-8")
            missing = root / "missing.mo"

            for source, code in (
                ("", "EMPTY_SOURCE_PATH"),
                (missing, "SOURCE_FILE_NOT_FOUND"),
                (wrong_extension, "UNSUPPORTED_SOURCE_EXTENSION"),
                (root, "SOURCE_PATH_NOT_FILE"),
            ):
                with self.subTest(source=source):
                    report = validate_modelica_compile_request(
                        ModelicaCompileRequest(source, "Source", output)
                    )
                    self.assertIn(("source_path", code), self._issue_keys(report))
            self.assertFalse(missing.exists())

    def test_output_directory_must_already_exist_and_be_directory(self) -> None:
        with self._valid_paths() as (source, output):
            missing = output.parent / "missing-output"
            file_path = output.parent / "not-a-directory"
            file_path.write_text("not a directory", encoding="utf-8")

            missing_report = validate_modelica_compile_request(
                ModelicaCompileRequest(source, "Source", missing)
            )
            file_report = validate_modelica_compile_request(
                ModelicaCompileRequest(source, "Source", file_path)
            )

        self.assertIn(("output_directory", "OUTPUT_DIRECTORY_NOT_FOUND"), self._issue_keys(missing_report))
        self.assertIn(("output_directory", "OUTPUT_PATH_NOT_DIRECTORY"), self._issue_keys(file_report))

    def test_only_fmi_2_cs_is_supported(self) -> None:
        with self._valid_paths() as (source, output):
            report = validate_modelica_compile_request(
                ModelicaCompileRequest(source, "Source", output, fmi_version="3.0", fmu_type="me")
            )

        self.assertEqual(
            {("fmi_version", "UNSUPPORTED_FMI_VERSION"), ("fmu_type", "UNSUPPORTED_FMU_TYPE")},
            set(self._issue_keys(report)),
        )

    def test_timeout_must_be_a_finite_number_in_range(self) -> None:
        with self._valid_paths() as (source, output):
            for timeout, code in (
                (0, "TIMEOUT_SECONDS_OUT_OF_RANGE"),
                (-1, "TIMEOUT_SECONDS_OUT_OF_RANGE"),
                (1801, "TIMEOUT_SECONDS_OUT_OF_RANGE"),
                (math.nan, "INVALID_TIMEOUT_SECONDS"),
                (math.inf, "INVALID_TIMEOUT_SECONDS"),
                (True, "INVALID_TIMEOUT_SECONDS"),
                ("120", "INVALID_TIMEOUT_SECONDS"),
            ):
                with self.subTest(timeout=timeout):
                    report = validate_modelica_compile_request(
                        ModelicaCompileRequest(source, "Source", output, timeout_seconds=timeout)
                    )
                    self.assertIn(("timeout_seconds", code), self._issue_keys(report))

    def test_compiler_path_is_optional_but_must_have_supported_nonempty_type(self) -> None:
        with self._valid_paths() as (source, output):
            for compiler_path, code in (("", "EMPTY_COMPILER_PATH"), (42, "INVALID_COMPILER_PATH")):
                with self.subTest(compiler_path=compiler_path):
                    report = validate_modelica_compile_request(
                        ModelicaCompileRequest(source, "Source", output, compiler_path=compiler_path)
                    )
                    self.assertIn(("compiler_path", code), self._issue_keys(report))

    def test_report_collects_multiple_field_issues(self) -> None:
        request = ModelicaCompileRequest(
            source_path="",
            model_name="bad name;",
            output_directory="",
            compiler_path="",
            fmi_version="3.0",
            fmu_type="me",
            timeout_seconds=0,
        )

        report = validate_modelica_compile_request(request)

        self.assertEqual(
            {
                "source_path",
                "model_name",
                "output_directory",
                "compiler_path",
                "fmi_version",
                "fmu_type",
                "timeout_seconds",
            },
            {issue.field for issue in report.issues},
        )

    def test_required_validation_maps_issues_to_existing_config_error(self) -> None:
        with self._valid_paths() as (source, output):
            request = ModelicaCompileRequest(source, "bad name", output)
            with self.assertRaises(EngineError) as raised:
                require_valid_modelica_compile_request(request)

        self.assertEqual(ErrorCode.CONFIG_ERROR, raised.exception.code)
        self.assertEqual("INVALID_MODEL_NAME", raised.exception.details["issues"][0]["code"])

    @staticmethod
    def _issue_keys(report) -> tuple[tuple[str, str], ...]:
        return tuple((issue.field, issue.code) for issue in report.issues)

    @staticmethod
    def _valid_paths():
        class _Paths:
            def __enter__(self):
                self._temporary_directory = tempfile.TemporaryDirectory()
                root = Path(self._temporary_directory.name)
                source = root / "Source.mo"
                source.write_text("model Source end Source;", encoding="utf-8")
                output = root / "output"
                output.mkdir()
                return source, output

            def __exit__(self, *args):
                self._temporary_directory.cleanup()

        return _Paths()
