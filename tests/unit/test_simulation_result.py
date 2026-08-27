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

        self.assertEqual(result.timestamps, (0.0, 0.08, 0.2))
        self.assertEqual(result.outputs, {"speed": (1.0, 1.5, 2.0)})
        self.assertEqual(result.completed_steps, 2)
        self.assertEqual(result.final_time, 0.2)
        self.assertTrue(session.terminated)
        self.assertTrue(session.closed)


if __name__ == "__main__":
    unittest.main()
