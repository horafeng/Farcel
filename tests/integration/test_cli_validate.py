import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from farcel.cli import main


FMU_FIXTURES = Path(__file__).resolve().parents[2] / "examples" / "fmus"


class ValidateCliIntegrationTests(unittest.TestCase):
    sample = FMU_FIXTURES / "VanDerPol.fmu"

    @unittest.skipUnless(sample.is_file(), "workspace Reference FMU is unavailable")
    def test_validates_real_fmu_configuration(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = main([
                "validate", str(self.sample), "--start-time", "0",
                "--stop-time", "2", "--step-size", "0.01",
                "--parameter", "mu=2.0", "--output", "x0",
            ])
        self.assertEqual(exit_code, 0)
        self.assertIn("validation successful", stdout.getvalue())

    @unittest.skipUnless(sample.is_file(), "workspace Reference FMU is unavailable")
    def test_cli_returns_nonzero_and_stable_issue_code(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr):
            exit_code = main(
                ["validate", str(self.sample), "--parameter", "missing=1"]
            )
        self.assertEqual(exit_code, 1)
        self.assertIn("CONFIG_ERROR", stderr.getvalue())
        self.assertIn("UNKNOWN_PARAMETER", stderr.getvalue())
