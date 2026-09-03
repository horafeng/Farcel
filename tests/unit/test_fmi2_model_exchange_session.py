from __future__ import annotations

import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from fmpy import platform as current_platform, sharedLibraryExtension

from farcel.application.model_exchange_problem import SessionModelExchangeProblem
from farcel.contracts import (
    CapabilitySet,
    EngineError,
    ErrorCode,
    InterfaceCapability,
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    VariableMetadata,
)
from farcel.infrastructure.fmpy.fmi2_model_exchange_session import (
    FmpyFmi2ModelExchangeSession,
    FmpyFmi2ModelExchangeSessionFactory,
)


def _metadata(*, include_model_exchange: bool = True) -> ModelMetadata:
    interfaces = (
        InterfaceCapability(
            interface_type=InterfaceType.MODEL_EXCHANGE,
            model_identifier="ModelExchangeTest",
        ),
    ) if include_model_exchange else (
        InterfaceCapability(
            interface_type=InterfaceType.CO_SIMULATION,
            model_identifier="CoSimulationTest",
            can_execute=True,
        ),
    )
    return ModelMetadata(
        model_id="model-exchange-test",
        source_path="model-exchange-test.fmu",
        fmi_version="2.0",
        model_name="ModelExchangeTest",
        interface_types=tuple(item.interface_type for item in interfaces),
        instantiation_token="model-exchange-guid",
        capabilities=CapabilitySet(can_execute=False),
        interface_capabilities=interfaces,
        platforms=(current_platform,),
        variables=(
            VariableMetadata("gain", 1, "Real", causality="parameter"),
            VariableMetadata("command", 2, "Real", causality="input"),
            VariableMetadata("speed", 3, "Real", causality="output"),
        ),
    )


class _FakeNativeModel:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.discrete_results: list[tuple[bool, bool, bool, bool, bool, float]] = []
        self.continuous_states = [1.0, -2.0]
        self.derivatives = [3.0, -4.0]
        self.event_indicators = [0.25]
        self.completed_result = (False, False)
        self.fail_initialization = False
        self.fail_terminate = False
        self.fail_derivatives = False

    def setupExperiment(self, **_: object) -> None:
        self.events.append("setup")

    def setReal(self, references, values) -> None:
        self.events.append(f"setReal:{references[0]}:{values[0]}")

    def enterInitializationMode(self) -> None:
        self.events.append("enterInitialization")
        if self.fail_initialization:
            raise RuntimeError("initialization failure")

    def exitInitializationMode(self) -> None:
        self.events.append("exitInitialization")

    def newDiscreteStates(self):
        self.events.append("newDiscreteStates")
        if self.discrete_results:
            return self.discrete_results.pop(0)
        return False, False, False, False, False, 0.0

    def enterContinuousTimeMode(self) -> None:
        self.events.append("enterContinuousTimeMode")

    def enterEventMode(self) -> None:
        self.events.append("enterEventMode")

    def setTime(self, time: float) -> None:
        self.events.append(f"setTime:{time}")

    def getContinuousStates(self, values, count: int) -> None:
        self.events.append("getContinuousStates")
        for index in range(count):
            values[index] = self.continuous_states[index]

    def setContinuousStates(self, values, count: int) -> None:
        self.events.append("setContinuousStates")
        self.continuous_states = [float(values[index]) for index in range(count)]

    def getDerivatives(self, values, count: int) -> None:
        self.events.append("getDerivatives")
        if self.fail_derivatives:
            raise RuntimeError("derivative failure")
        for index in range(count):
            values[index] = self.derivatives[index]

    def getEventIndicators(self, values, count: int) -> None:
        self.events.append("getEventIndicators")
        for index in range(count):
            values[index] = self.event_indicators[index]

    def completedIntegratorStep(self):
        self.events.append("completedIntegratorStep")
        return self.completed_result

    def getReal(self, references):
        self.events.append(f"getReal:{references[0]}")
        return [42.0]

    def terminate(self) -> None:
        self.events.append("terminate")
        if self.fail_terminate:
            raise RuntimeError("termination failure")

    def freeInstance(self) -> None:
        self.events.append("freeInstance")


class Fmi2ModelExchangeSessionTests(unittest.TestCase):
    def test_factory_accepts_only_available_fmi2_model_exchange(self) -> None:
        factory = FmpyFmi2ModelExchangeSessionFactory()
        expected = object()
        with patch(
            "farcel.infrastructure.fmpy.fmi2_model_exchange_session."
            "_fmi2_model_exchange_library_is_present",
            return_value=True,
        ), patch.object(
            FmpyFmi2ModelExchangeSession, "open", return_value=expected
        ) as open_session:
            result = factory.create(_metadata(), SimulationConfig())

        self.assertIs(result, expected)
        open_session.assert_called_once()

        for metadata, expected_code in (
            (ModelMetadata(
                model_id="fmi3", source_path="fmi3.fmu", fmi_version="3.0",
                model_name="Fmi3", interface_types=(InterfaceType.MODEL_EXCHANGE,),
            ), ErrorCode.UNSUPPORTED_FMI),
            (_metadata(include_model_exchange=False), ErrorCode.UNSUPPORTED_INTERFACE),
        ):
            with self.subTest(metadata=metadata.model_id), self.assertRaises(EngineError) as raised:
                factory.create(metadata, SimulationConfig())
            self.assertEqual(raised.exception.code, expected_code)

        missing_platform = replace(_metadata(), platforms=())
        with self.assertRaises(EngineError) as raised:
            factory.create(missing_platform, SimulationConfig())
        self.assertEqual(raised.exception.code, ErrorCode.PLATFORM_BINARY_MISSING)

    def test_factory_rejects_missing_model_exchange_library_before_extraction(self) -> None:
        factory = FmpyFmi2ModelExchangeSessionFactory()
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "interface-specific.fmu"
            co_simulation_library = (
                f"binaries/{current_platform}/CoSimulationTest{sharedLibraryExtension}"
            )
            with zipfile.ZipFile(archive_path, "w") as archive:
                # A non-empty placeholder is sufficient: this guard must run
                # before FMPy loads or instantiates any native library.
                archive.writestr(co_simulation_library, b"co-simulation-only")

            metadata = replace(_metadata(), source_path=str(archive_path))
            with patch.object(FmpyFmi2ModelExchangeSession, "open") as open_session, patch(
                "farcel.infrastructure.fmpy.fmi2_model_exchange_session."
                "tempfile.mkdtemp"
            ) as extraction_directory:
                with self.assertRaises(EngineError) as raised:
                    factory.create(metadata, SimulationConfig())

        self.assertEqual(raised.exception.code, ErrorCode.PLATFORM_BINARY_MISSING)
        self.assertEqual(raised.exception.details["platform"], current_platform)
        self.assertEqual(
            raised.exception.details["model_identifier"], "ModelExchangeTest"
        )
        self.assertEqual(
            raised.exception.details["expected_library_archive_path"],
            f"binaries/{current_platform}/ModelExchangeTest{sharedLibraryExtension}",
        )
        open_session.assert_not_called()
        extraction_directory.assert_not_called()

    def test_initialize_performs_bounded_discrete_iteration_and_maps_initialization(self) -> None:
        native = _FakeNativeModel()
        native.discrete_results = [
            (True, False, False, False, False, 0.0),
            (True, False, True, True, True, 1.25),
            (False, False, False, True, True, 2.5),
        ]
        session, extraction, temporary = self._session(
            native,
            SimulationConfig(
                parameters={"gain": 2.0}, initial_inputs={"command": 1.5}
            ),
        )
        try:
            initialization = session.initialize()

            self.assertEqual(initialization.continuous_state_count, 2)
            self.assertEqual(initialization.event_indicator_count, 1)
            self.assertTrue(initialization.continuous_states_changed)
            self.assertTrue(initialization.next_event_time_defined)
            self.assertEqual(initialization.next_event_time, 2.5)
            self.assertEqual(native.events, [
                "setup", "setReal:1:2.0", "setReal:2:1.5",
                "enterInitialization", "exitInitialization", "newDiscreteStates",
                "newDiscreteStates", "newDiscreteStates", "enterContinuousTimeMode",
            ])
        finally:
            session.terminate()
            session.close()
            temporary.cleanup()
        self.assertFalse(extraction.exists())

    def test_initial_iteration_limit_and_termination_request_do_not_enter_continuous_time_mode(self) -> None:
        native = _FakeNativeModel()
        native.discrete_results = [(True, False, False, False, False, 0.0)] * 3
        session, extraction, temporary = self._session(native)
        try:
            with patch(
                "farcel.infrastructure.fmpy.fmi2_model_exchange_session._MAX_INITIAL_DISCRETE_STATE_ITERATIONS",
                2,
            ), self.assertRaises(EngineError) as raised:
                session.initialize()
            self.assertEqual(raised.exception.code, ErrorCode.INITIALIZATION_ERROR)
            self.assertEqual(raised.exception.details["iteration_count"], 2)
        finally:
            session.close()
            temporary.cleanup()
        self.assertFalse(extraction.exists())

        native = _FakeNativeModel()
        native.discrete_results = [(False, True, False, False, True, 3.0)]
        session, extraction, temporary = self._session(native)
        try:
            initialization = session.initialize()
            self.assertTrue(initialization.terminate_requested)
            self.assertNotIn("enterContinuousTimeMode", native.events)
            session.terminate()
        finally:
            session.close()
            temporary.cleanup()
        self.assertFalse(extraction.exists())

    def test_continuous_time_primitives_and_event_update_use_farcel_values(self) -> None:
        native = _FakeNativeModel()
        native.completed_result = (True, True)
        native.discrete_results = [
            (False, False, False, False, False, 0.0),
            (True, False, True, False, True, 4.0),
        ]
        session, extraction, temporary = self._session(native, SimulationConfig(selected_outputs=("speed",)))
        try:
            session.initialize()
            session.set_time(0.5)
            with self.assertRaises(EngineError) as raised:
                session.set_time(float("nan"))
            self.assertEqual(raised.exception.code, ErrorCode.STEP_ERROR)
            self.assertEqual(session.get_continuous_states(), (1.0, -2.0))
            session.set_continuous_states((2.0, 3.0))
            self.assertEqual(native.continuous_states, [2.0, 3.0])
            self.assertEqual(session.get_derivatives(), (3.0, -4.0))
            self.assertEqual(session.get_event_indicators(), (0.25,))
            self.assertTrue(session.completed_integrator_step().enter_event_mode)
            self.assertTrue(session.completed_integrator_step().terminate_requested)
            self.assertEqual(session.read_outputs(), {"speed": 42.0})
            session.enter_event_mode()
            update = session.update_discrete_states()
            self.assertTrue(update.discrete_states_need_update)
            self.assertTrue(update.nominals_changed)
            self.assertTrue(update.next_event_time_defined)
            self.assertEqual(update.next_event_time, 4.0)
            session.enter_continuous_time_mode()

            with self.assertRaises(EngineError) as raised:
                session.set_continuous_states((1.0,))
            self.assertEqual(raised.exception.code, ErrorCode.STEP_ERROR)
        finally:
            session.terminate()
            session.close()
            temporary.cleanup()
        self.assertFalse(extraction.exists())

    def test_zero_dimension_primitives_and_cleanup_failure_paths(self) -> None:
        native = _FakeNativeModel()
        session, extraction, temporary = self._session(
            native, continuous_state_count=0, event_indicator_count=0
        )
        try:
            session.initialize()
            self.assertEqual(session.get_continuous_states(), ())
            session.set_continuous_states(())
            self.assertEqual(session.get_derivatives(), ())
            self.assertEqual(session.get_event_indicators(), ())
            self.assertNotIn("getContinuousStates", native.events)
            self.assertNotIn("getDerivatives", native.events)
            self.assertNotIn("getEventIndicators", native.events)
        finally:
            session.terminate()
            session.close()
            temporary.cleanup()
        self.assertFalse(extraction.exists())

        native = _FakeNativeModel()
        native.fail_terminate = True
        session, extraction, temporary = self._session(native)
        try:
            session.initialize()
            with self.assertRaises(EngineError) as raised:
                session.terminate()
            self.assertEqual(raised.exception.code, ErrorCode.TERMINATION_ERROR)
            session.close()
            session.close()
            self.assertEqual(native.events.count("freeInstance"), 1)
        finally:
            temporary.cleanup()
        self.assertFalse(extraction.exists())

        native = _FakeNativeModel()
        native.fail_initialization = True
        session, extraction, temporary = self._session(native)
        try:
            with self.assertRaises(EngineError) as raised:
                session.initialize()
            self.assertEqual(raised.exception.code, ErrorCode.INITIALIZATION_ERROR)
            session.close()
        finally:
            temporary.cleanup()
        self.assertFalse(extraction.exists())
        self.assertIn("freeInstance", native.events)

        native = _FakeNativeModel()
        native.fail_derivatives = True
        session, extraction, temporary = self._session(native)
        try:
            session.initialize()
            with self.assertRaises(EngineError) as raised:
                session.get_derivatives()
            self.assertEqual(raised.exception.code, ErrorCode.STEP_ERROR)
            session.close()
        finally:
            temporary.cleanup()
        self.assertFalse(extraction.exists())

    def test_session_model_exchange_problem_delegates_without_native_dependencies(self) -> None:
        session = Mock()
        session.get_continuous_states.return_value = (1.0, 2.0)
        session.get_derivatives.return_value = (3.0, 4.0)
        session.get_event_indicators.return_value = (0.0,)
        problem = SessionModelExchangeProblem(session)

        self.assertEqual(problem.get_initial_states(), (1.0, 2.0))
        problem.set_state(0.5, (5.0, 6.0))
        self.assertEqual(problem.get_derivatives(), (3.0, 4.0))
        self.assertEqual(problem.get_event_indicators(), (0.0,))
        session.set_time.assert_called_once_with(0.5)
        session.set_continuous_states.assert_called_once_with((5.0, 6.0))

    @staticmethod
    def _session(
        native: _FakeNativeModel,
        config: SimulationConfig | None = None,
        *,
        continuous_state_count: int = 2,
        event_indicator_count: int = 1,
    ) -> tuple[FmpyFmi2ModelExchangeSession, Path, tempfile.TemporaryDirectory[str]]:
        temporary = tempfile.TemporaryDirectory()
        extraction = Path(temporary.name) / "extracted"
        extraction.mkdir()
        return (
            FmpyFmi2ModelExchangeSession(
                _metadata(),
                config or SimulationConfig(),
                native,
                extraction,
                continuous_state_count,
                event_indicator_count,
            ),
            extraction,
            temporary,
        )
