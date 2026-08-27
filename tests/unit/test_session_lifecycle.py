import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from farcel.application.engine import FarcelEngine
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import (
    CapabilitySet,
    InterfaceCapability,
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    VariableMetadata,
)
from farcel.infrastructure.fmpy import session as fmpy_session
from farcel.infrastructure.fmpy.session import FmpyFmi2Session


def executable_metadata() -> ModelMetadata:
    return ModelMetadata(
        model_id="runtime-test",
        source_path="runtime-test.fmu",
        fmi_version="2.0",
        model_name="RuntimeTest",
        interface_types=(InterfaceType.CO_SIMULATION,),
        executable_interface=InterfaceType.CO_SIMULATION,
        capabilities=CapabilitySet(can_execute=True),
        interface_capabilities=(
            InterfaceCapability(
                interface_type=InterfaceType.CO_SIMULATION,
                model_identifier="RuntimeTest",
                can_execute=True,
                can_handle_variable_step=True,
            ),
        ),
        variables=(),
    )


class FakeNativeFmu:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.fail_initialization = False
        self.fail_parameter = False
        self.fail_step = False
        self.fail_output = False
        self.fail_terminate = False
        self.fail_free = False

    def setupExperiment(self, **_: object) -> None:
        self.events.append("setup")

    def setReal(self, _: list[int], values: list[float]) -> None:
        self.events.append(f"setReal:{values[0]}")
        if self.fail_parameter:
            raise RuntimeError("native parameter set failed")

    def enterInitializationMode(self) -> None:
        self.events.append("enterInitialization")
        if self.fail_initialization:
            raise RuntimeError("native initialization failed")

    def exitInitializationMode(self) -> None:
        self.events.append("exitInitialization")

    def doStep(self, **_: object) -> None:
        self.events.append("doStep")
        if self.fail_step:
            raise RuntimeError("native step failed")

    def getReal(self, _: list[int]) -> list[float]:
        self.events.append("getReal")
        if self.fail_output:
            raise RuntimeError("native output read failed")
        return [1.25]

    def terminate(self) -> None:
        self.events.append("terminate")
        if self.fail_terminate:
            raise RuntimeError("native terminate failed")

    def freeInstance(self) -> None:
        self.events.append("freeInstance")
        if self.fail_free:
            raise RuntimeError("native free failed")


class SessionLifecycleTests(unittest.TestCase):
    def test_instantiate_failure_is_mapped_and_removes_extraction_directory(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            extraction = Path(parent) / "extracted"
            extraction.mkdir()
            with (
                patch(
                    "farcel.infrastructure.fmpy.session.tempfile.mkdtemp",
                    return_value=str(extraction),
                ),
                patch("farcel.infrastructure.fmpy.session.extract"),
                patch.object(
                    fmpy_session.FMU2Slave,
                    "__init__",
                    side_effect=RuntimeError("native instantiate setup failed"),
                ),
            ):
                with self.assertRaises(EngineError) as raised:
                    fmpy_session.FmpyFmi2Session.open(
                        executable_metadata(), SimulationConfig(), "RuntimeTest"
                    )
            self.assertEqual(raised.exception.code, ErrorCode.INSTANTIATION_ERROR)
            self.assertFalse(extraction.exists())

    def test_parameter_is_set_before_initialization_mode(self) -> None:
        metadata = replace(
            executable_metadata(),
            variables=(
                VariableMetadata(
                    "gain", 7, "Real", causality="parameter", variability="fixed"
                ),
            ),
        )
        native = FakeNativeFmu()
        with tempfile.TemporaryDirectory() as parent:
            extraction = Path(parent) / "extracted"
            extraction.mkdir()
            session = FmpyFmi2Session(
                metadata,
                SimulationConfig(parameters={"gain": 2.5}),
                native,
                extraction,
            )
            session.initialize()
            session.terminate()
            session.close()
        self.assertEqual(
            native.events[:4],
            ["setup", "setReal:2.5", "enterInitialization", "exitInitialization"],
        )

    def test_parameter_failure_is_mapped(self) -> None:
        metadata = replace(
            executable_metadata(),
            variables=(
                VariableMetadata(
                    "gain", 7, "Real", causality="parameter", variability="fixed"
                ),
            ),
        )
        native = FakeNativeFmu()
        native.fail_parameter = True
        with tempfile.TemporaryDirectory() as parent:
            extraction = Path(parent) / "extracted"
            extraction.mkdir()
            session = FmpyFmi2Session(
                metadata,
                SimulationConfig(parameters={"gain": 2.5}),
                native,
                extraction,
            )
            with self.assertRaises(EngineError) as raised:
                session.initialize()
            self.assertEqual(raised.exception.code, ErrorCode.PARAMETER_SET_ERROR)
            session.close()

    def test_step_failure_is_mapped_to_stable_error(self) -> None:
        native = FakeNativeFmu()
        native.fail_step = True
        with tempfile.TemporaryDirectory() as parent:
            extraction = Path(parent) / "extracted"
            extraction.mkdir()
            session = FmpyFmi2Session(
                executable_metadata(), SimulationConfig(), native, extraction
            )
            session.initialize()
            with self.assertRaises(EngineError) as raised:
                session.step(0.0, 0.01)
            self.assertEqual(raised.exception.code, ErrorCode.STEP_ERROR)
            session.close()
            self.assertFalse(extraction.exists())
        self.assertIn("freeInstance", native.events)

    def test_output_read_failure_is_mapped_and_application_cleans_up(self) -> None:
        metadata = replace(
            executable_metadata(),
            variables=(
                VariableMetadata(
                    "speed", 8, "Real", causality="output", variability="continuous"
                ),
            ),
        )
        native = FakeNativeFmu()
        native.fail_output = True
        config = SimulationConfig(selected_outputs=("speed",))
        with tempfile.TemporaryDirectory() as parent:
            extraction = Path(parent) / "extracted"
            extraction.mkdir()
            lifecycle = FmpyFmi2Session(metadata, config, native, extraction)
            factory = Mock()
            factory.create.return_value = lifecycle
            importer = Mock()
            importer.load.return_value = metadata

            with self.assertRaises(EngineError) as raised:
                FarcelEngine(importer, factory).run_fmu("runtime-test.fmu", config)

            self.assertEqual(raised.exception.code, ErrorCode.OUTPUT_READ_ERROR)
            self.assertNotIn("native output read failed", str(raised.exception))
            self.assertFalse(extraction.exists())
        self.assertIn("terminate", native.events)
        self.assertIn("freeInstance", native.events)

    def test_termination_failure_is_mapped_and_close_still_frees(self) -> None:
        native = FakeNativeFmu()
        native.fail_terminate = True
        with tempfile.TemporaryDirectory() as parent:
            extraction = Path(parent) / "extracted"
            extraction.mkdir()
            session = FmpyFmi2Session(
                executable_metadata(), SimulationConfig(), native, extraction
            )
            session.initialize()
            with self.assertRaises(EngineError) as raised:
                session.terminate()
            self.assertEqual(raised.exception.code, ErrorCode.TERMINATION_ERROR)
            session.close()
            self.assertFalse(extraction.exists())
        self.assertIn("freeInstance", native.events)

    def test_cleanup_failure_is_reported_after_directory_removal_attempt(self) -> None:
        native = FakeNativeFmu()
        native.fail_free = True
        with tempfile.TemporaryDirectory() as parent:
            extraction = Path(parent) / "extracted"
            extraction.mkdir()
            session = FmpyFmi2Session(
                executable_metadata(), SimulationConfig(), native, extraction
            )
            with self.assertRaises(EngineError) as raised:
                session.close()
            self.assertEqual(raised.exception.code, ErrorCode.CLEANUP_ERROR)
            self.assertFalse(extraction.exists())

    def test_initialization_failure_is_closed_by_application(self) -> None:
        native = FakeNativeFmu()
        native.fail_initialization = True
        with tempfile.TemporaryDirectory() as parent:
            extraction = Path(parent) / "extracted"
            extraction.mkdir()
            lifecycle = FmpyFmi2Session(
                executable_metadata(), SimulationConfig(), native, extraction
            )
            factory = Mock()
            factory.create.return_value = lifecycle
            importer = Mock()
            importer.load.return_value = executable_metadata()
            engine = FarcelEngine(importer, factory)

            with self.assertRaises(EngineError) as raised:
                engine.run_fmu("runtime-test.fmu", SimulationConfig())

            self.assertEqual(raised.exception.code, ErrorCode.INITIALIZATION_ERROR)
            self.assertFalse(extraction.exists())
        self.assertIn("freeInstance", native.events)

    def test_interrupt_still_closes_session(self) -> None:
        lifecycle = Mock()
        lifecycle.initialize.side_effect = KeyboardInterrupt()
        factory = Mock()
        factory.create.return_value = lifecycle
        importer = Mock()
        importer.load.return_value = executable_metadata()
        engine = FarcelEngine(importer, factory)

        with self.assertRaises(KeyboardInterrupt):
            engine.run_fmu("runtime-test.fmu", SimulationConfig())

        lifecycle.close.assert_called_once_with()
