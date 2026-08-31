from __future__ import annotations

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
from farcel.infrastructure.fmpy import fmi3_session
from farcel.infrastructure.fmpy.fmi3_session import FmpyFmi3Session


def executable_metadata(
    *, event_mode: bool = False, early_return: bool = False
) -> ModelMetadata:
    return ModelMetadata(
        model_id="fmi3-runtime-test",
        source_path="fmi3-runtime-test.fmu",
        fmi_version="3.0",
        model_name="Fmi3RuntimeTest",
        interface_types=(InterfaceType.CO_SIMULATION,),
        executable_interface=InterfaceType.CO_SIMULATION,
        instantiation_token="token",
        capabilities=CapabilitySet(
            can_execute=True,
            supports_event_mode=event_mode,
            supports_early_return=early_return,
        ),
        interface_capabilities=(
            InterfaceCapability(
                interface_type=InterfaceType.CO_SIMULATION,
                model_identifier="Fmi3RuntimeTest",
                can_execute=True,
                can_handle_variable_step=True,
                supports_event_mode=event_mode,
                supports_early_return=early_return,
            ),
        ),
        variables=(
            VariableMetadata(
                "gain", 1, "Float64", causality="parameter", variability="fixed"
            ),
            VariableMetadata(
                "speed", 2, "Float64", causality="output", variability="continuous"
            ),
        ),
    )


class FakeNativeFmi3:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.fail_parameter = False
        self.fail_initialization = False
        self.interrupt_initialization = False
        self.fail_step = False
        self.fail_output = False
        self.fail_terminate = False
        self.fail_free = False
        self.fail_event_mode = False
        self.fail_discrete_states_at: int | None = None
        self.fail_step_mode_at: int | None = None
        self.advanced_step_result = False
        self.event_encountered = False
        self.terminate_requested = False
        self.discrete_state_results: list[
            tuple[bool, bool, bool, bool, bool, float]
        ] = []
        self.discrete_state_calls = 0
        self.step_mode_calls = 0

    def setFloat64(self, _: list[int], values: list[float]) -> None:
        self.events.append(f"setFloat64:{values[0]}")
        if self.fail_parameter:
            raise RuntimeError("native FMI 3 parameter failure")

    def __getattr__(self, name: str):
        if name.startswith("set"):
            def setter(_: list[int], values: list[object]) -> None:
                self.events.append(f"{name}:{values[0]}")
            return setter
        raise AttributeError(name)

    def enterInitializationMode(self, **_: object) -> None:
        self.events.append("enterInitialization")
        if self.interrupt_initialization:
            raise KeyboardInterrupt()
        if self.fail_initialization:
            raise RuntimeError("native FMI 3 initialization failure")

    def exitInitializationMode(self) -> None:
        self.events.append("exitInitialization")

    def enterEventMode(self) -> None:
        self.events.append("enterEventMode")
        if self.fail_event_mode:
            raise RuntimeError("native FMI 3 Event Mode failure")

    def updateDiscreteStates(self) -> tuple[bool, bool, bool, bool, bool, float]:
        self.events.append("updateDiscreteStates")
        self.discrete_state_calls += 1
        if self.fail_discrete_states_at == self.discrete_state_calls:
            raise RuntimeError("native FMI 3 discrete state failure")
        if self.discrete_state_results:
            return self.discrete_state_results.pop(0)
        return False, False, False, False, False, 0.0

    def enterStepMode(self) -> None:
        self.events.append("enterStepMode")
        self.step_mode_calls += 1
        if self.fail_step_mode_at == self.step_mode_calls:
            raise RuntimeError("native FMI 3 Step Mode failure")

    def doStep(
        self, *, currentCommunicationPoint: float, communicationStepSize: float
    ) -> tuple[bool, bool, bool, float]:
        self.events.append("doStep")
        if self.fail_step:
            raise RuntimeError("native FMI 3 step failure")
        reached_time = currentCommunicationPoint + communicationStepSize
        if self.advanced_step_result:
            return False, False, True, reached_time / 2
        return (
            self.event_encountered,
            self.terminate_requested,
            False,
            reached_time,
        )

    def getFloat64(self, _: list[int]) -> list[float]:
        self.events.append("getFloat64")
        if self.fail_output:
            raise RuntimeError("native FMI 3 output failure")
        return [1.25]

    def terminate(self) -> None:
        self.events.append("terminate")
        if self.fail_terminate:
            raise RuntimeError("native FMI 3 termination failure")

    def freeInstance(self) -> None:
        self.events.append("freeInstance")
        if self.fail_free:
            raise RuntimeError("native FMI 3 free failure")


class FakeOpenedFmi3:
    instances: list["FakeOpenedFmi3"] = []

    def __init__(self, **_: object) -> None:
        self.instantiate_options: dict[str, object] = {}
        self.freed = False
        self.instances.append(self)

    def instantiate(self, **options: object) -> None:
        self.instantiate_options = options

    def freeInstance(self) -> None:
        self.freed = True


class Fmi3SessionLifecycleTests(unittest.TestCase):
    def test_instantiate_failure_is_mapped_and_removes_directory(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            extraction = Path(parent) / "extracted"
            extraction.mkdir()
            with (
                patch(
                    "farcel.infrastructure.fmpy.fmi3_session.tempfile.mkdtemp",
                    return_value=str(extraction),
                ),
                patch("farcel.infrastructure.fmpy.fmi3_session.extract"),
                patch.object(
                    fmi3_session.FMU3Slave,
                    "__init__",
                    side_effect=RuntimeError("native FMI 3 load failure"),
                ),
            ):
                with self.assertRaises(EngineError) as raised:
                    FmpyFmi3Session.open(
                        executable_metadata(), SimulationConfig(), "Fmi3RuntimeTest"
                    )

            self.assertEqual(raised.exception.code, ErrorCode.INSTANTIATION_ERROR)
            self.assertFalse(extraction.exists())

    def test_instantiate_uses_event_and_early_return_capabilities(self) -> None:
        for event_mode, early_return in ((False, False), (True, True)):
            with self.subTest(event_mode=event_mode, early_return=early_return):
                FakeOpenedFmi3.instances.clear()
                with tempfile.TemporaryDirectory() as parent:
                    extraction = Path(parent) / "extracted"
                    extraction.mkdir()
                    with (
                        patch(
                            "farcel.infrastructure.fmpy.fmi3_session.tempfile.mkdtemp",
                            return_value=str(extraction),
                        ),
                        patch("farcel.infrastructure.fmpy.fmi3_session.extract"),
                        patch(
                            "farcel.infrastructure.fmpy.fmi3_session.FMU3Slave",
                            FakeOpenedFmi3,
                        ),
                    ):
                        session = FmpyFmi3Session.open(
                            executable_metadata(
                                event_mode=event_mode,
                                early_return=early_return,
                            ),
                            SimulationConfig(),
                            "Fmi3RuntimeTest",
                        )

                    native = FakeOpenedFmi3.instances[0]
                    self.assertEqual(
                        native.instantiate_options["eventModeUsed"], event_mode
                    )
                    self.assertEqual(
                        native.instantiate_options["earlyReturnAllowed"], early_return
                    )
                    session.close()
                    self.assertTrue(native.freed)
                    self.assertFalse(extraction.exists())

    def test_parameter_is_set_before_fmi3_initialization(self) -> None:
        native = FakeNativeFmi3()
        with tempfile.TemporaryDirectory() as parent:
            extraction = Path(parent) / "extracted"
            extraction.mkdir()
            session = FmpyFmi3Session(
                executable_metadata(),
                SimulationConfig(parameters={"gain": 2.5}),
                native,
                extraction,
            )
            session.initialize()
            session.terminate()
            session.close()

        self.assertEqual(
            native.events[:3],
            ["setFloat64:2.5", "enterInitialization", "exitInitialization"],
        )

    def test_event_mode_initialization_and_runtime_event_follow_fmi3_order(self) -> None:
        native = FakeNativeFmi3()
        native.event_encountered = True
        native.discrete_state_results = [
            (True, False, False, False, False, 0.0),
            (False, False, False, False, False, 0.0),
            (True, False, False, False, False, 0.0),
            (False, False, False, False, False, 0.0),
        ]
        session, extraction, temporary = self._session(
            native, metadata=executable_metadata(event_mode=True)
        )
        try:
            session.initialize()
            result = session.step(0.0, 0.1)
            session.terminate()
            session.close()

            self.assertTrue(result.event_encountered)
            self.assertEqual(
                native.events,
                [
                    "enterInitialization",
                    "exitInitialization",
                    "updateDiscreteStates",
                    "updateDiscreteStates",
                    "enterStepMode",
                    "doStep",
                    "enterEventMode",
                    "updateDiscreteStates",
                    "updateDiscreteStates",
                    "enterStepMode",
                    "terminate",
                    "freeInstance",
                ],
            )
            self.assertFalse(extraction.exists())
        finally:
            temporary.cleanup()

    def test_early_return_is_preserved_when_capability_allows_it(self) -> None:
        native = FakeNativeFmi3()
        native.advanced_step_result = True
        session, extraction, temporary = self._session(
            native, metadata=executable_metadata(early_return=True)
        )
        try:
            session.initialize()
            result = session.step(0.0, 0.1)
            session.close()

            self.assertTrue(result.early_return)
            self.assertEqual(result.requested_time, 0.1)
            self.assertEqual(result.reached_time, 0.05)
            self.assertFalse(extraction.exists())
        finally:
            temporary.cleanup()

    def test_event_iteration_overflow_is_stable(self) -> None:
        native = FakeNativeFmi3()
        native.event_encountered = True
        native.discrete_state_results = [
            (False, False, False, False, False, 0.0),
            (True, False, False, False, False, 0.0),
            (True, False, False, False, False, 0.0),
        ]
        session, extraction, temporary = self._session(
            native, metadata=executable_metadata(event_mode=True)
        )
        try:
            session.initialize()
            with patch(
                "farcel.infrastructure.fmpy.fmi3_session._MAX_EVENT_ITERATIONS", 2
            ):
                with self.assertRaises(EngineError) as raised:
                    session.step(0.0, 0.1)
            self.assertEqual(raised.exception.code, ErrorCode.STEP_ERROR)
            self.assertEqual(raised.exception.details["event_iteration_count"], 2)
            session.close()
            self.assertFalse(extraction.exists())
        finally:
            temporary.cleanup()

    def test_terminate_requested_from_do_step_is_stable(self) -> None:
        native = FakeNativeFmi3()
        native.terminate_requested = True
        session, extraction, temporary = self._session(native)
        try:
            session.initialize()
            with self.assertRaises(EngineError) as raised:
                session.step(0.0, 0.1)
            self.assertEqual(raised.exception.code, ErrorCode.STEP_ERROR)
            self.assertTrue(raised.exception.details["terminate_requested"])
            session.close()
            self.assertFalse(extraction.exists())
        finally:
            temporary.cleanup()

    def test_terminate_requested_from_event_update_is_stable(self) -> None:
        native = FakeNativeFmi3()
        native.event_encountered = True
        native.discrete_state_results = [
            (False, False, False, False, False, 0.0),
            (False, True, False, False, False, 0.0),
        ]
        session, extraction, temporary = self._session(
            native, metadata=executable_metadata(event_mode=True)
        )
        try:
            session.initialize()
            with self.assertRaises(EngineError) as raised:
                session.step(0.0, 0.1)
            self.assertEqual(raised.exception.code, ErrorCode.STEP_ERROR)
            self.assertTrue(raised.exception.details["terminate_requested"])
            session.close()
            self.assertFalse(extraction.exists())
        finally:
            temporary.cleanup()

    def test_runtime_event_mode_failures_clean_up_application_resources(self) -> None:
        for failure in ("enter_event", "update_discrete", "enter_step"):
            with self.subTest(failure=failure):
                native = FakeNativeFmi3()
                native.event_encountered = True
                if failure == "enter_event":
                    native.fail_event_mode = True
                elif failure == "update_discrete":
                    native.fail_discrete_states_at = 2
                else:
                    native.fail_step_mode_at = 2
                session, extraction, temporary = self._session(
                    native, metadata=executable_metadata(event_mode=True)
                )
                factory = Mock()
                factory.create.return_value = session
                importer = Mock()
                importer.load.return_value = executable_metadata(event_mode=True)
                engine = FarcelEngine(importer, factory)
                try:
                    with self.assertRaises(EngineError) as raised:
                        engine.run_fmu("fmi3-test.fmu", SimulationConfig())
                    self.assertEqual(raised.exception.code, ErrorCode.STEP_ERROR)
                    self.assertIn("terminate", native.events)
                    self.assertIn("freeInstance", native.events)
                    self.assertFalse(extraction.exists())
                    self.assertEqual(engine._sessions, {})
                finally:
                    temporary.cleanup()

    def test_parameter_failure_is_stable_and_resources_are_freed(self) -> None:
        native = FakeNativeFmi3()
        native.fail_parameter = True
        session, extraction, temporary = self._session(
            native, SimulationConfig(parameters={"gain": 2.5})
        )
        try:
            with self.assertRaises(EngineError) as raised:
                session.initialize()
            self.assertEqual(raised.exception.code, ErrorCode.PARAMETER_SET_ERROR)
            session.close()
            self.assertFalse(extraction.exists())
            self.assertIn("freeInstance", native.events)
        finally:
            temporary.cleanup()

    def test_fmi3_initial_inputs_use_matching_scalar_setters(self) -> None:
        scalar_types = (
            "Float32", "Float64", "Int8", "UInt8", "Int16", "UInt16",
            "Int32", "UInt32", "Int64", "UInt64", "Boolean", "String",
            "Enumeration",
        )
        model = replace(
            executable_metadata(),
            variables=tuple(
                VariableMetadata(f"input_{kind}", index + 10, kind, causality="input")
                for index, kind in enumerate(scalar_types)
            ),
        )
        values = {
            "Float32": 1.25, "Float64": 2.5, "Int8": -8, "UInt8": 8,
            "Int16": -16, "UInt16": 16, "Int32": -32, "UInt32": 32,
            "Int64": -64, "UInt64": 64, "Boolean": True, "String": "ready",
            "Enumeration": 3,
        }
        native = FakeNativeFmi3()
        with tempfile.TemporaryDirectory() as parent:
            extraction = Path(parent) / "extracted"
            extraction.mkdir()
            session = FmpyFmi3Session(
                model,
                SimulationConfig(initial_inputs={f"input_{kind}": values[kind] for kind in scalar_types}),
                native,
                extraction,
            )
            session.initialize()
            session.terminate()
            session.close()
        for kind in scalar_types:
            setter_kind = "Int64" if kind == "Enumeration" else kind
            self.assertIn(f"set{setter_kind}:{values[kind]}", native.events)

    def test_step_failure_and_unsupported_early_return_are_stable(self) -> None:
        for failure in ("exception", "early_return"):
            with self.subTest(failure=failure):
                native = FakeNativeFmi3()
                native.fail_step = failure == "exception"
                native.advanced_step_result = failure == "early_return"
                session, extraction, temporary = self._session(native)
                try:
                    session.initialize()
                    with self.assertRaises(EngineError) as raised:
                        session.step(0.0, 0.1)
                    self.assertEqual(raised.exception.code, ErrorCode.STEP_ERROR)
                    if failure == "exception":
                        self.assertNotIn(
                            "native FMI 3 step failure", str(raised.exception)
                        )
                    session.close()
                    self.assertFalse(extraction.exists())
                finally:
                    temporary.cleanup()

    def test_output_failure_is_stable_and_application_cleans_up(self) -> None:
        native = FakeNativeFmi3()
        native.fail_output = True
        config = SimulationConfig(selected_outputs=("speed",))
        session, extraction, temporary = self._session(native, config)
        try:
            factory = Mock()
            factory.create.return_value = session
            importer = Mock()
            importer.load.return_value = executable_metadata()

            with self.assertRaises(EngineError) as raised:
                FarcelEngine(importer, factory).run_fmu("fmi3-test.fmu", config)

            self.assertEqual(raised.exception.code, ErrorCode.OUTPUT_READ_ERROR)
            self.assertNotIn("native FMI 3 output failure", str(raised.exception))
            self.assertIn("terminate", native.events)
            self.assertIn("freeInstance", native.events)
            self.assertFalse(extraction.exists())
        finally:
            temporary.cleanup()

    def test_initialization_failure_and_interrupt_both_close_resources(self) -> None:
        for failure in ("exception", "interrupt"):
            with self.subTest(failure=failure):
                native = FakeNativeFmi3()
                native.fail_initialization = failure == "exception"
                native.interrupt_initialization = failure == "interrupt"
                session, extraction, temporary = self._session(native)
                factory = Mock()
                factory.create.return_value = session
                importer = Mock()
                importer.load.return_value = executable_metadata()
                try:
                    if failure == "exception":
                        with self.assertRaises(EngineError) as raised:
                            FarcelEngine(importer, factory).run_fmu(
                                "fmi3-test.fmu", SimulationConfig()
                            )
                        self.assertEqual(
                            raised.exception.code, ErrorCode.INITIALIZATION_ERROR
                        )
                    else:
                        with self.assertRaises(KeyboardInterrupt):
                            FarcelEngine(importer, factory).run_fmu(
                                "fmi3-test.fmu", SimulationConfig()
                            )
                    self.assertIn("freeInstance", native.events)
                    self.assertFalse(extraction.exists())
                finally:
                    temporary.cleanup()

    def test_termination_failure_still_allows_free(self) -> None:
        native = FakeNativeFmi3()
        native.fail_terminate = True
        session, extraction, temporary = self._session(native)
        try:
            session.initialize()
            with self.assertRaises(EngineError) as raised:
                session.terminate()
            self.assertEqual(raised.exception.code, ErrorCode.TERMINATION_ERROR)
            session.close()
            self.assertIn("freeInstance", native.events)
            self.assertFalse(extraction.exists())
        finally:
            temporary.cleanup()

    def test_cleanup_failure_is_stable_and_directory_is_still_removed(self) -> None:
        native = FakeNativeFmi3()
        native.fail_free = True
        session, extraction, temporary = self._session(native)
        try:
            with self.assertRaises(EngineError) as raised:
                session.close()
            self.assertEqual(raised.exception.code, ErrorCode.CLEANUP_ERROR)
            self.assertFalse(extraction.exists())
        finally:
            temporary.cleanup()

    @staticmethod
    def _session(
        native: FakeNativeFmi3,
        config: SimulationConfig | None = None,
        metadata: ModelMetadata | None = None,
    ) -> tuple[FmpyFmi3Session, Path, tempfile.TemporaryDirectory[str]]:
        temporary = tempfile.TemporaryDirectory()
        extraction = Path(temporary.name) / "extracted"
        extraction.mkdir()
        return (
            FmpyFmi3Session(
                metadata or executable_metadata(),
                config or SimulationConfig(),
                native,
                extraction,
            ),
            extraction,
            temporary,
        )


if __name__ == "__main__":
    unittest.main()
