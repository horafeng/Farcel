from __future__ import annotations

import unittest
from threading import get_ident
from unittest.mock import Mock

from farcel import ResultChunk, RunControl
from farcel.application.engine import FarcelEngine
from farcel.contracts import (
    CapabilitySet,
    EngineError,
    ErrorCode,
    InterfaceCapability,
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    SimulationResult,
    SimulationState,
    StepResult,
    StepStatus,
    VariableMetadata,
)


def executable_metadata() -> ModelMetadata:
    return ModelMetadata(
        model_id="chunk-test",
        source_path="chunk-test.fmu",
        fmi_version="2.0",
        model_name="ChunkTest",
        interface_types=(InterfaceType.CO_SIMULATION,),
        executable_interface=InterfaceType.CO_SIMULATION,
        capabilities=CapabilitySet(can_execute=True),
        interface_capabilities=(
            InterfaceCapability(
                interface_type=InterfaceType.CO_SIMULATION,
                can_execute=True,
                can_handle_variable_step=True,
            ),
        ),
        variables=(VariableMetadata("speed", 1, "Real", causality="output"),),
    )


class _Session:
    def __init__(self) -> None:
        self.current_time = 0.0
        self.output_reads = 0
        self.terminated = False
        self.closed = False

    def initialize(self) -> None:
        pass

    def step(self, current_time: float, step_size: float) -> StepResult:
        self.current_time = current_time + step_size
        return StepResult(
            requested_time=self.current_time,
            reached_time=self.current_time,
            step_size=step_size,
        )

    def read_outputs(self) -> dict[str, float]:
        self.output_reads += 1
        return {"speed": self.current_time}

    def terminate(self) -> None:
        self.terminated = True

    def close(self) -> None:
        self.closed = True


class _Factory:
    def __init__(self) -> None:
        self.sessions: list[_Session] = []

    def create(self, metadata, config) -> _Session:
        session = _Session()
        self.sessions.append(session)
        return session


class _FailingSession(_Session):
    def step(self, current_time: float, step_size: float) -> StepResult:
        if current_time >= 0.02:
            return StepResult(
                requested_time=current_time,
                reached_time=current_time,
                step_size=step_size,
                status=StepStatus.FAILED,
            )
        return super().step(current_time, step_size)


class _FailingFactory(_Factory):
    def create(self, metadata, config) -> _FailingSession:
        session = _FailingSession()
        self.sessions.append(session)
        return session


class ResultChunkStreamingTests(unittest.TestCase):
    def test_samples_stream_as_two_two_one_chunks_and_match_result(self) -> None:
        engine, _ = self._engine()
        chunks: list[ResultChunk] = []

        result = engine.run_fmu(
            "chunk-test.fmu",
            self._five_sample_config(),
            on_result_chunk=chunks.append,
            result_chunk_size=2,
        )

        self.assertIsInstance(chunks[0], ResultChunk)
        self.assertEqual([chunk.sequence for chunk in chunks], [0, 1, 2])
        self.assertEqual([len(chunk.time) for chunk in chunks], [2, 2, 1])
        self.assertEqual([chunk.final_chunk for chunk in chunks], [False, False, True])
        self.assertEqual(chunks[0].time[0], 0.0)
        self._assert_chunks_match_result(chunks, result)

    def test_exact_chunk_boundary_uses_last_nonempty_chunk_as_final(self) -> None:
        engine, _ = self._engine()
        chunks: list[ResultChunk] = []

        result = engine.run_fmu(
            "chunk-test.fmu",
            SimulationConfig(
                stop_time=0.15,
                communication_step=0.05,
                selected_outputs=("speed",),
            ),
            on_result_chunk=chunks.append,
            result_chunk_size=2,
        )

        self.assertEqual([len(chunk.time) for chunk in chunks], [2, 2])
        self.assertEqual([chunk.final_chunk for chunk in chunks], [False, True])
        self._assert_chunks_match_result(chunks, result)

    def test_each_run_has_new_run_id_and_sequence_restarts(self) -> None:
        engine, _ = self._engine()
        first: list[ResultChunk] = []
        second: list[ResultChunk] = []

        engine.run_fmu(
            "chunk-test.fmu", self._five_sample_config(), on_result_chunk=first.append
        )
        engine.run_fmu(
            "chunk-test.fmu", self._five_sample_config(), on_result_chunk=second.append
        )

        self.assertNotEqual(first[0].run_id, second[0].run_id)
        self.assertTrue(all(chunk.run_id == first[0].run_id for chunk in first))
        self.assertTrue(all(chunk.run_id == second[0].run_id for chunk in second))
        self.assertEqual(first[0].sequence, 0)
        self.assertEqual(second[0].sequence, 0)

    def test_streaming_without_selected_outputs_sends_timeline_only(self) -> None:
        engine, _ = self._engine()
        chunks: list[ResultChunk] = []

        result = engine.run_fmu(
            "chunk-test.fmu",
            SimulationConfig(stop_time=0.2, communication_step=0.01, output_interval=0.05),
            on_result_chunk=chunks.append,
            result_chunk_size=2,
        )

        self.assertEqual(result.outputs, {})
        self.assertTrue(all(chunk.columns == {} for chunk in chunks))
        self._assert_chunks_match_result(chunks, result)

    def test_stopped_final_sample_is_in_final_chunk(self) -> None:
        control = RunControl()
        engine, _ = self._engine()
        chunks: list[ResultChunk] = []

        result = engine.run_fmu(
            "chunk-test.fmu",
            self._five_sample_config(),
            control=control,
            on_progress=lambda progress: (
                control.request_stop() if progress.current_time >= 0.07 else None
            ),
            on_result_chunk=chunks.append,
            result_chunk_size=2,
        )

        self.assertEqual(result.completion_state, SimulationState.STOPPED)
        self.assertAlmostEqual(result.final_time, 0.07)
        self.assertTrue(chunks[-1].final_chunk)
        self.assertAlmostEqual(chunks[-1].time[-1], 0.07)
        self._assert_chunks_match_result(chunks, result)

    def test_streaming_does_not_add_output_reads(self) -> None:
        plain_engine, plain_factory = self._engine()
        stream_engine, stream_factory = self._engine()

        plain_engine.run_fmu("chunk-test.fmu", self._five_sample_config())
        stream_engine.run_fmu(
            "chunk-test.fmu",
            self._five_sample_config(),
            on_result_chunk=lambda chunk: None,
            result_chunk_size=2,
        )

        self.assertEqual(
            plain_factory.sessions[0].output_reads, stream_factory.sessions[0].output_reads
        )
        self.assertEqual(plain_factory.sessions[0].output_reads, 5)

    def test_chunk_callback_runs_on_run_thread(self) -> None:
        engine, _ = self._engine()
        callback_threads: list[int] = []

        engine.run_fmu(
            "chunk-test.fmu",
            self._five_sample_config(),
            on_result_chunk=lambda chunk: callback_threads.append(get_ident()),
            result_chunk_size=1,
        )

        self.assertEqual(callback_threads, [get_ident()] * len(callback_threads))

    def test_chunk_callback_error_is_stable_and_cleans_up(self) -> None:
        engine, factory = self._engine()

        with self.assertRaises(EngineError) as raised:
            engine.run_fmu(
                "chunk-test.fmu",
                self._five_sample_config(),
                on_result_chunk=lambda chunk: (_ for _ in ()).throw(RuntimeError("chunk boom")),
                result_chunk_size=1,
            )

        self.assertEqual(raised.exception.code, ErrorCode.INTERNAL_ERROR)
        self.assertEqual(
            raised.exception.details["chunk_callback_diagnostic"], "chunk boom"
        )
        self.assertTrue(factory.sessions[0].terminated)
        self.assertTrue(factory.sessions[0].closed)
        self.assertEqual(engine._sessions, {})

    def test_runtime_error_does_not_emit_an_extra_final_chunk(self) -> None:
        metadata = executable_metadata()
        importer = type("Importer", (), {"load": lambda _, path: metadata})()
        factory = _FailingFactory()
        chunks: list[ResultChunk] = []

        with self.assertRaises(EngineError) as raised:
            FarcelEngine(importer, factory).run_fmu(
                "chunk-test.fmu",
                SimulationConfig(
                    stop_time=0.1,
                    communication_step=0.01,
                    output_interval=0.01,
                    selected_outputs=("speed",),
                ),
                on_result_chunk=chunks.append,
                result_chunk_size=2,
            )

        self.assertEqual(raised.exception.code, ErrorCode.STEP_ERROR)
        self.assertEqual([len(chunk.time) for chunk in chunks], [2])
        self.assertFalse(chunks[0].final_chunk)
        self.assertTrue(factory.sessions[0].terminated)
        self.assertTrue(factory.sessions[0].closed)

    def test_pre_cancel_does_not_emit_chunks_or_load_fmu(self) -> None:
        control = RunControl()
        control.request_stop()
        importer = Mock()
        factory = Mock()
        chunks: list[ResultChunk] = []

        with self.assertRaises(EngineError) as raised:
            FarcelEngine(importer, factory).run_fmu(
                "chunk-test.fmu",
                self._five_sample_config(),
                control=control,
                on_result_chunk=chunks.append,
            )

        self.assertEqual(raised.exception.code, ErrorCode.CANCELLED)
        self.assertEqual(chunks, [])
        importer.load.assert_not_called()
        factory.create.assert_not_called()

    def test_invalid_chunk_sizes_fail_before_loading(self) -> None:
        for size in (0, -1, True, 1.5, "2"):
            with self.subTest(size=size):
                importer = Mock()
                factory = Mock()
                with self.assertRaises(EngineError) as raised:
                    FarcelEngine(importer, factory).run_fmu(
                        "chunk-test.fmu", self._five_sample_config(), result_chunk_size=size
                    )
                self.assertEqual(raised.exception.code, ErrorCode.CONFIG_ERROR)
                self.assertEqual(
                    raised.exception.details["issues"][0]["field"], "result_chunk_size"
                )
                self.assertEqual(
                    raised.exception.details["issues"][0]["code"],
                    "INVALID_RESULT_CHUNK_SIZE",
                )
                importer.load.assert_not_called()
                factory.create.assert_not_called()

    @staticmethod
    def _five_sample_config() -> SimulationConfig:
        return SimulationConfig(
            stop_time=0.2,
            communication_step=0.01,
            output_interval=0.05,
            selected_outputs=("speed",),
        )

    @staticmethod
    def _engine() -> tuple[FarcelEngine, _Factory]:
        metadata = executable_metadata()
        importer = type("Importer", (), {"load": lambda _, path: metadata})()
        factory = _Factory()
        return FarcelEngine(importer, factory), factory

    def _assert_chunks_match_result(
        self, chunks: list[ResultChunk], result: SimulationResult
    ) -> None:
        self.assertEqual(
            tuple(time for chunk in chunks for time in chunk.time), result.timestamps
        )
        for name, values in result.outputs.items():
            self.assertEqual(
                tuple(value for chunk in chunks for value in chunk.columns[name]), values
            )
        self.assertEqual([chunk.sequence for chunk in chunks], list(range(len(chunks))))
        self.assertEqual(sum(chunk.final_chunk for chunk in chunks), 1)
        self.assertTrue(chunks[-1].final_chunk)
