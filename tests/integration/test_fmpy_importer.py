import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from farcel.cli import main
from farcel.contracts.models import InterfaceType
from farcel.infrastructure.fmpy import FmpyImporter


FMI2_XML = """<?xml version="1.0" encoding="UTF-8"?>
<fmiModelDescription
  fmiVersion="2.0"
  modelName="MetadataTest"
  guid="{12345678-1234-1234-1234-123456789abc}"
  variableNamingConvention="flat"
  numberOfEventIndicators="0">
  <ModelExchange modelIdentifier="MetadataTestME"/>
  <CoSimulation
    modelIdentifier="MetadataTestCS"
    canHandleVariableCommunicationStepSize="true"/>
  <DefaultExperiment startTime="0" stopTime="5" tolerance="1e-6" stepSize="0.1"/>
  <ModelVariables>
    <ScalarVariable name="gain" valueReference="1" causality="parameter" variability="fixed" initial="exact">
      <Real start="2.5" min="0" max="10"/>
    </ScalarVariable>
  </ModelVariables>
  <ModelStructure/>
</fmiModelDescription>
"""


FMI3_SCHEDULED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<fmiModelDescription
  fmiVersion="3.0"
  modelName="ScheduledMetadataTest"
  instantiationToken="12345678-1234-1234-1234-123456789abc">
  <ScheduledExecution modelIdentifier="ScheduledMetadataTest"/>
  <DefaultExperiment startTime="0" stopTime="2"/>
  <ModelVariables>
    <Float64 name="time" valueReference="0" causality="independent" variability="continuous"/>
    <Float64 name="gain" valueReference="1" causality="parameter" variability="fixed" initial="exact" start="3.5"/>
  </ModelVariables>
  <ModelStructure/>
</fmiModelDescription>
"""


class FmpyImporterIntegrationTests(unittest.TestCase):
    def test_maps_fmi2_interfaces_defaults_variables_and_executability(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fmu = self._write_fmu(Path(directory), "fmi2.fmu", FMI2_XML, with_dll=True)
            metadata = FmpyImporter().load(fmu)

        self.assertEqual(metadata.fmi_version, "2.0")
        self.assertEqual(
            metadata.interface_types,
            (InterfaceType.CO_SIMULATION, InterfaceType.MODEL_EXCHANGE),
        )
        self.assertTrue(metadata.capabilities.can_execute)
        self.assertEqual(metadata.executable_interface, InterfaceType.CO_SIMULATION)
        self.assertEqual(metadata.default_experiment.stop_time, 5.0)
        self.assertEqual(metadata.variables[0].start, 2.5)
        self.assertEqual(metadata.variables[0].minimum, 0.0)
        self.assertEqual(metadata.variables[0].maximum, 10.0)
        self.assertEqual(
            metadata.interface_capabilities[0].model_identifier,
            "MetadataTestCS",
        )

    def test_maps_fmi3_scheduled_execution_without_claiming_it_can_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fmu = self._write_fmu(
                Path(directory), "fmi3.fmu", FMI3_SCHEDULED_XML, with_dll=False
            )
            metadata = FmpyImporter().load(fmu)

        self.assertEqual(metadata.fmi_version, "3.0")
        self.assertEqual(
            metadata.interface_types, (InterfaceType.SCHEDULED_EXECUTION,)
        )
        self.assertFalse(metadata.capabilities.can_execute)
        self.assertIsNone(metadata.executable_interface)
        self.assertIn("可解析", metadata.diagnostics[0])

    def test_parseable_co_simulation_without_host_binary_is_not_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fmu = self._write_fmu(
                Path(directory), "source-only.fmu", FMI2_XML, with_dll=False
            )
            metadata = FmpyImporter().load(fmu)

        self.assertEqual(metadata.fmi_version, "2.0")
        self.assertFalse(metadata.capabilities.can_execute)
        self.assertIsNone(metadata.executable_interface)
        self.assertIn("缺少当前平台二进制", metadata.diagnostics[0])

    def test_cli_inspect_json_uses_the_real_adapter_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fmu = self._write_fmu(Path(directory), "inspect.fmu", FMI2_XML, with_dll=True)
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["inspect", str(fmu), "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["model_name"], "MetadataTest")
        self.assertEqual(payload["interface_types"], ["co_simulation", "model_exchange"])
        self.assertTrue(payload["capabilities"]["can_execute"])

    @staticmethod
    def _write_fmu(
        directory: Path, name: str, model_description: str, *, with_dll: bool
    ) -> Path:
        path = directory / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("modelDescription.xml", model_description)
            if with_dll:
                archive.writestr("binaries/win64/MetadataTestCS.dll", b"")
        return path


class WorkspaceSampleIntegrationTests(unittest.TestCase):
    sample = Path(r"D:\fmu示例文件\VanDerPol.fmu")

    @unittest.skipUnless(sample.is_file(), "workspace Reference FMU is unavailable")
    def test_inspects_workspace_reference_fmu(self) -> None:
        metadata = FmpyImporter().load(self.sample)

        self.assertEqual(metadata.fmi_version, "2.0")
        self.assertEqual(metadata.model_name, "Van der Pol oscillator")
        self.assertIn(InterfaceType.CO_SIMULATION, metadata.interface_types)
        self.assertIn("win64", metadata.platforms)
        self.assertGreater(len(metadata.variables), 0)
