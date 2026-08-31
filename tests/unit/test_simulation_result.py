import unittest

from farcel.application.engine import FarcelEngine
from farcel.contracts.models import (
    CapabilitySet,
    InterfaceCapability,
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    StepResult,
    VariableMetadata,
)


def executable_metadata() -> ModelMetadata:
    return ModelMetadata(
        model_id="result-test",
        source_path="result-test.fmu",
        fmi_version="2.0",
        model_name="ResultTest",
        interface_types=(InterfaceType.CO_SIMULATION,),
        executable_interface=InterfaceType.CO_SIMULATION,
        capabilities=CapabilitySet(can_execute=True),
        interface_capabilities=(
            InterfaceCapability(
                interface_type=InterfaceType.CO_SIMULATION,
                model_identifier="ResultTest",
                can_execute=True,
                can_handle_variable_step=True,
            ),
        ),
        variables=(
            VariableMetadata(
                "speed", 1, "Real", causality="output", variability="continuous"
            ),
        ),
    )


class ActualTimeSession:
    def __init__(self) -> None:
        self.reached_times = iter((0.08, 0.2))
        self.values = iter((1.0, 1.5, 2.0))
        self.terminated = False
        self.closed = False

    def initialize(self) -> None:
        pass

    def step(self, current_time: float, step_size: float) -> StepResult:
        return StepResult(
            requested_time=current_time + step_size,
            reached_time=next(self.reached_times),
            step_size=step_size,
        )

    def read_outputs(self) -> dict[str, float]:
        return {"speed": next(self.values)}

    def terminate(self) -> None:
        self.terminated = True

    def close(self) -> None:
        self.closed = True


class SimulationResultTests(unittest.TestCase):
    def test_result_uses_session_reached_times_instead_of_step_index(self) -> None:
        metadata = executable_metadata()
        importer = type("Importer", (), {"load": lambda _, path: metadata})()
        session = ActualTimeSession()
        factory = type("Factory", (), {"create": lambda _, metadata, config: session})()

        result = FarcelEngine(importer, factory).run_fmu(
            "result-test.fmu",
            SimulationConfig(
                start_time=0.0,
                stop_time=0.2,
                communication_step=0.1,
                selected_outputs=("speed",),
            ),
        )

        self.assertEqual(result.timestamps, (0.0, 0.2))
        self.assertEqual(result.outputs, {"speed": (1.0, 1.5)})
        self.assertEqual(result.completed_steps, 2)
        self.assertEqual(result.sample_count, 2)
        self.assertEqual(result.final_time, 0.2)
        self.assertTrue(session.terminated)
        self.assertTrue(session.closed)

    def test_records_output_interval_independently_from_communication_steps(self) -> None:
        metadata = executable_metadata()
        importer = type("Importer", (), {"load": lambda _, path: metadata})()
        session = _CommunicationPointSession()
        factory = type("Factory", (), {"create": lambda _, metadata, config: session})()

        result = FarcelEngine(importer, factory).run_fmu(
            "result-test.fmu",
            SimulationConfig(
                start_time=0.0,
                stop_time=0.2,
                communication_step=0.01,
                output_interval=0.05,
                selected_outputs=("speed",),
            ),
        )

        self.assertEqual(result.completed_steps, 20)
        self._assert_timestamps(result.timestamps, (0.0, 0.05, 0.1, 0.15, 0.2))
        self.assertEqual(result.sample_count, 5)
        self.assertEqual(len(result.outputs["speed"]), 5)
        for actual, expected in zip(session.step_times, (index / 100 for index in range(20))):
            self.assertAlmostEqual(actual, expected)

    def test_final_time_is_sampled_when_not_on_output_interval(self) -> None:
        metadata = executable_metadata()
        importer = type("Importer", (), {"load": lambda _, path: metadata})()
        session = _CommunicationPointSession()
        factory = type("Factory", (), {"create": lambda _, metadata, config: session})()

        result = FarcelEngine(importer, factory).run_fmu(
            "result-test.fmu",
            SimulationConfig(
                start_time=0.0,
                stop_time=1.0,
                communication_step=0.1,
                output_interval=0.3,
                selected_outputs=("speed",),
            ),
        )

        self.assertEqual(result.completed_steps, 10)
        self._assert_timestamps(result.timestamps, (0.0, 0.3, 0.6, 0.9, 1.0))
        self.assertEqual(result.sample_count, 5)

    def test_empty_selected_outputs_keeps_sampled_timeline_without_reads(self) -> None:
        metadata = executable_metadata()
        importer = type("Importer", (), {"load": lambda _, path: metadata})()
        session = _CommunicationPointSession()
        factory = type("Factory", (), {"create": lambda _, metadata, config: session})()

        result = FarcelEngine(importer, factory).run_fmu(
            "result-test.fmu",
            SimulationConfig(
                stop_time=0.2,
                communication_step=0.01,
                output_interval=0.05,
            ),
        )

        self._assert_timestamps(result.timestamps, (0.0, 0.05, 0.1, 0.15, 0.2))
        self.assertEqual(result.outputs, {})
        self.assertEqual(session.output_reads, 0)

    def _assert_timestamps(
        self, actual: tuple[float, ...], expected: tuple[float, ...]
    ) -> None:
        self.assertEqual(len(actual), len(expected))
        for observed, target in zip(actual, expected):
            self.assertAlmostEqual(observed, target)


class _CommunicationPointSession:
    def __init__(self) -> None:
        self.step_times: list[float] = []
        self.output_reads = 0

    def initialize(self) -> None:
        pass

    def step(self, current_time: float, step_size: float) -> StepResult:
        self.step_times.append(current_time)
        return StepResult(
            requested_time=current_time + step_size,
            reached_time=current_time + step_size,
            step_size=step_size,
        )

    def read_outputs(self) -> dict[str, float]:
        self.output_reads += 1
        return {"speed": float(self.output_reads)}

    def terminate(self) -> None:
        pass

    def close(self) -> None:
        pass


if __name__ == "__main__":
    unittest.main()
