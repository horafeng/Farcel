from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from farcel import RunControl, create_backend
from farcel.application.runners import ModelExchangeRunner
from farcel.contracts import (
    EngineError,
    ErrorCode,
    InputUpdate,
    InterfaceType,
    SimulationConfig,
    SimulationState,
)
from farcel.infrastructure.fmpy import (
    FmpyCvodeSolverFactory,
    FmpyFmi2ModelExchangeSessionFactory,
    FmpyImporter,
)


FMU_FIXTURES = Path(__file__).resolve().parents[2] / "examples" / "fmus"


class Fmi2ModelExchangeReleaseTests(unittest.TestCase):
    """Release hardening for the internal-only FMI2 ME runner."""

    van_der_pol = FMU_FIXTURES / "VanDerPol.fmu"
    stair = FMU_FIXTURES / "Stair.fmu"
    bouncing_ball = FMU_FIXTURES / "BouncingBall-fmi2.fmu"
    feedthrough = FMU_FIXTURES / "Feedthrough-fmi2.fmu"

    @classmethod
    def setUpClass(cls) -> None:
        cls.importer = FmpyImporter()
        cls.session_factory = FmpyFmi2ModelExchangeSessionFactory()
        cls.solver_factory = FmpyCvodeSolverFactory()

    def _runner(self) -> ModelExchangeRunner:
        return ModelExchangeRunner(self.session_factory, self.solver_factory)

    def _run(self, path: Path, config: SimulationConfig, **kwargs):
        return self._runner().run(path, self.importer.load(path), config, **kwargs)

    def test_bouncing_ball_state_event_runner_detects_bounce_and_continues(self) -> None:
        result = self._run(
            self.bouncing_ball,
            SimulationConfig(
                stop_time=1.0,
                communication_step=0.05,
                selected_outputs=("h", "v"),
            ),
        )

        self.assertIs(result.completion_state, SimulationState.COMPLETED)
        self.assertEqual(result.completed_steps, 20)
        self.assertAlmostEqual(result.final_time, 1.0, places=12)
        self.assertTrue(all(height >= 0.0 for height in result.outputs["h"]))
        bounce_index = next(
            index for index, velocity in enumerate(result.outputs["v"]) if velocity > 0.0
        )
        self.assertTrue(any(velocity < 0.0 for velocity in result.outputs["v"][:bounce_index]))
        self.assertGreater(result.outputs["h"][bounce_index], 0.0)

    def test_feedthrough_zero_state_runner_handles_real_input_event(self) -> None:
        config = SimulationConfig(
            stop_time=0.04,
            communication_step=0.01,
            initial_inputs={"Float64_continuous_input": 1.0},
            input_schedule=(InputUpdate(0.02, {"Float64_continuous_input": 4.0}),),
            selected_outputs=("Float64_continuous_output",),
        )
        session = self.session_factory.create(self.importer.load(self.feedthrough), config)
        try:
            initialization = session.initialize()
            self.assertEqual(initialization.continuous_state_count, 0)
        finally:
            session.terminate()
            session.close()

        result = self._run(self.feedthrough, config)
        self.assertIs(result.completion_state, SimulationState.COMPLETED)
        self.assertEqual(result.completed_steps, 4)
        self.assertEqual(result.timestamps, (0.0, 0.01, 0.02, 0.03, 0.04))
        self.assertEqual(
            result.outputs["Float64_continuous_output"], (1.0, 1.0, 4.0, 4.0, 4.0)
        )

    def test_van_der_pol_non_zero_start_partial_checkpoint_interval_and_tolerance(self) -> None:
        non_zero = self._run(
            self.van_der_pol,
            SimulationConfig(
                start_time=1.0,
                stop_time=1.05,
                communication_step=0.01,
                relative_tolerance=1e-6,
                selected_outputs=("x0",),
            ),
        )
        self.assertEqual(non_zero.timestamps, (1.0, 1.01, 1.02, 1.03, 1.04, 1.05))
        self.assertEqual(non_zero.completed_steps, 5)

        partial = self._run(
            self.van_der_pol,
            SimulationConfig(stop_time=0.05, communication_step=0.02, selected_outputs=("x0",)),
        )
        self.assertEqual(partial.timestamps, (0.0, 0.02, 0.04, 0.05))
        self.assertEqual(partial.completed_steps, 3)

        interval = self._run(
            self.van_der_pol,
            SimulationConfig(
                stop_time=0.05,
                communication_step=0.01,
                output_interval=0.02,
                selected_outputs=("x0",),
            ),
        )
        self.assertEqual(interval.timestamps, (0.0, 0.02, 0.04, 0.05))

    def test_repeated_van_der_pol_is_deterministic_and_leaves_no_temp_directories(self) -> None:
        config = SimulationConfig(stop_time=0.05, communication_step=0.01, selected_outputs=("x0",))
        before = set(Path(tempfile.gettempdir()).glob("farcel-fmi2-me-*"))
        final_values = []
        run_ids = []
        for _ in range(20):
            chunks = []
            result = self._run(self.van_der_pol, config, on_result_chunk=chunks.append, result_chunk_size=2)
            self.assertIs(result.completion_state, SimulationState.COMPLETED)
            self.assertEqual((result.completed_steps, result.sample_count), (5, 6))
            self.assertEqual(sum(chunk.final_chunk for chunk in chunks), 1)
            self.assertEqual(chunks[0].sequence, 0)
            final_values.append(result.outputs["x0"][-1])
            run_ids.append(chunks[0].run_id)
        after = set(Path(tempfile.gettempdir()).glob("farcel-fmi2-me-*"))
        self.assertEqual(after - before, set())
        self.assertEqual(len(run_ids), len(set(run_ids)))
        self.assertTrue(all(math.isclose(value, final_values[0], rel_tol=0.0, abs_tol=1e-10) for value in final_values))

    def test_stair_issue_882_release_regression_repeats_after_time_event(self) -> None:
        config = SimulationConfig(stop_time=1.2, communication_step=0.2, selected_outputs=("counter",))
        for _ in range(5):
            result = self._run(self.stair, config)
            self.assertIs(result.completion_state, SimulationState.COMPLETED)
            self.assertEqual((result.completed_steps, result.outputs["counter"][-1]), (6, 2))
            self.assertAlmostEqual(result.final_time, 1.2, places=12)

    def test_normal_stop_normal_and_callback_failure_do_not_pollute_next_run(self) -> None:
        config = SimulationConfig(stop_time=0.05, communication_step=0.01, selected_outputs=("x0",))
        normal = self._run(self.van_der_pol, config)
        control = RunControl()

        def request_stop(progress) -> None:
            if progress.current_time >= 0.02:
                control.request_stop()

        stopped = self._run(self.van_der_pol, config, control=control, on_progress=request_stop)
        self.assertIs(stopped.completion_state, SimulationState.STOPPED)
        self.assertEqual((stopped.final_time, stopped.completed_steps), (0.02, 2))
        with self.assertRaises(EngineError) as raised:
            self._run(
                self.van_der_pol,
                config,
                on_result_chunk=lambda _: (_ for _ in ()).throw(RuntimeError("chunk failure")),
                result_chunk_size=1,
            )
        self.assertEqual(raised.exception.code, ErrorCode.INTERNAL_ERROR)
        after_failure = self._run(self.van_der_pol, config)
        self.assertIs(normal.completion_state, SimulationState.COMPLETED)
        self.assertIs(after_failure.completion_state, SimulationState.COMPLETED)

    def test_public_me_execution_and_metadata_policy_remain_closed(self) -> None:
        backend = create_backend()
        metadata = backend.load_fmu(self.van_der_pol)
        self.assertIs(metadata.executable_interface, InterfaceType.CO_SIMULATION)
        capability = next(
            item for item in metadata.interface_capabilities if item.interface_type is InterfaceType.MODEL_EXCHANGE
        )
        self.assertFalse(capability.can_execute)
        with self.assertRaises(EngineError) as raised:
            backend.run_fmu(
                self.van_der_pol,
                SimulationConfig(execution_interface=InterfaceType.MODEL_EXCHANGE),
            )
        self.assertEqual(raised.exception.code, ErrorCode.CONFIG_ERROR)
        self.assertEqual(raised.exception.details["issues"][0]["code"], ErrorCode.UNSUPPORTED_INTERFACE.value)
