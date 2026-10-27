"""Opt-in real OMC coverage for Modelica source, FMU parameter and input boundaries."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import math
import os
from pathlib import Path
import tempfile
import unittest

from farcel import create_backend
from farcel.contracts import (
    EngineError,
    ErrorCode,
    InputUpdate,
    ModelicaAssetStagingRequest,
    ModelicaCompileRequest,
    SimulationConfig,
    SimulationProject,
    SimulationState,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MODEL = REPOSITORY_ROOT / "examples" / "modelica" / "FarcelFirstOrder.mo"
DEFAULT_OMC = Path(r"D:\OpenModelica\bin\omc.exe")


@unittest.skipUnless(
    os.environ.get("FARCEL_RUN_OPENMODELICA_INTEGRATION") == "1",
    "需要显式设置 FARCEL_RUN_OPENMODELICA_INTEGRATION=1 并安装 OpenModelica",
)
class ModelicaParameterBoundaryRealIntegrationTests(unittest.TestCase):
    def test_same_fmu_parameter_overrides_change_response_without_recompilation(self) -> None:
        with self._temporary_root() as root:
            backend = create_backend()
            compiled = self._compile_once(backend, root)
            fmu_sha256 = _sha256(compiled.fmu_path)
            source_sha256 = _sha256(MODEL)
            metadata = backend.load_fmu(compiled.fmu_path)

            fast = backend.run_fmu(compiled.fmu_path, _config(T=0.5, u=1.0))
            slow = backend.run_fmu(compiled.fmu_path, _config(T=1.0, u=1.0))

            _assert_complete_series(self, fast)
            _assert_complete_series(self, slow)
            self.assertLessEqual(_maximum_error(fast, T=0.5, u=1.0), 0.03)
            self.assertLessEqual(_maximum_error(slow, T=1.0, u=1.0), 0.03)
            self.assertGreater(float(fast.outputs["y"][50]), float(slow.outputs["y"][50]))
            self.assertEqual(fmu_sha256, _sha256(compiled.fmu_path))
            self.assertEqual(source_sha256, _sha256(MODEL))
            self.assertEqual("parameter", next(item.causality for item in metadata.variables if item.name == "T"))

    def test_initial_input_amplitude_is_independent_from_parameter_override(self) -> None:
        with self._temporary_root() as root:
            backend = create_backend()
            compiled = self._compile_once(backend, root)

            unit_input = backend.run_fmu(compiled.fmu_path, _config(T=0.5, u=1.0))
            doubled_input = backend.run_fmu(compiled.fmu_path, _config(T=0.5, u=2.0))

            _assert_complete_series(self, unit_input)
            _assert_complete_series(self, doubled_input)
            self.assertLessEqual(_maximum_error(unit_input, T=0.5, u=1.0), 0.03)
            self.assertLessEqual(_maximum_error(doubled_input, T=0.5, u=2.0), 0.03)
            self.assertGreater(float(doubled_input.outputs["y"][-1]), float(unit_input.outputs["y"][-1]))
            self.assertAlmostEqual(
                2.0 * float(unit_input.outputs["y"][-1]),
                float(doubled_input.outputs["y"][-1]),
                delta=0.03,
            )

    def test_unsupported_compile_options_are_config_errors_before_omc_startup(self) -> None:
        with self._temporary_root() as root:
            backend = create_backend()
            output = root / "output"
            output.mkdir()
            missing_omc = root / "must-not-be-started.exe"
            cases = (
                ("fmi_version", ModelicaCompileRequest(MODEL, "FarcelFirstOrder", output, missing_omc, "3.0")),
                ("fmu_type", ModelicaCompileRequest(MODEL, "FarcelFirstOrder", output, missing_omc, "2.0", "me")),
            )
            for expected_field, request in cases:
                with self.subTest(field=expected_field):
                    with self.assertRaises(EngineError) as raised:
                        backend.compile_modelica(request)
                    self.assertEqual(ErrorCode.CONFIG_ERROR, raised.exception.code)
                    self.assertIn(
                        expected_field,
                        {issue["field"] for issue in raised.exception.details["issues"]},
                    )
            self.assertFalse(missing_omc.exists())

    def test_project_registration_is_unchanged_by_parameter_overrides(self) -> None:
        with self._temporary_root() as root:
            project_root = root / "Project"
            project_root.mkdir()
            backend = create_backend()
            backend.save_project(project_root, SimulationProject("parameter-boundary", "参数边界"))
            project = backend.migrate_project_schema(project_root).project
            registration = backend.compile_project_modelica(
                project,
                ModelicaAssetStagingRequest(
                    project_root=project_root,
                    source_path=MODEL,
                    model_name="FarcelFirstOrder",
                    compiler_path=_omc_path(),
                ),
            )
            persisted_before = backend.open_project(project_root)
            asset = registration.staged_build.model_asset
            provenance = registration.staged_build.provenance
            fmu = project_root / asset.relative_path
            fmu_sha256 = _sha256(fmu)

            first = backend.run_fmu(fmu, _config(T=0.5, u=1.0))
            second = backend.run_fmu(fmu, _config(T=1.0, u=1.0))

            persisted_after = backend.open_project(project_root)
            _assert_complete_series(self, first)
            _assert_complete_series(self, second)
            self.assertLessEqual(_maximum_error(first, T=0.5, u=1.0), 0.03)
            self.assertLessEqual(_maximum_error(second, T=1.0, u=1.0), 0.03)
            self.assertEqual(persisted_before.model_assets, persisted_after.model_assets)
            self.assertEqual(persisted_before.modelica_build_provenance, persisted_after.modelica_build_provenance)
            self.assertEqual((provenance,), backend.list_project_modelica_builds(persisted_after))
            self.assertEqual(1, len(persisted_after.model_assets))
            self.assertEqual(fmu_sha256, _sha256(fmu))
            self.assertEqual(asset.sha256, fmu_sha256)

    def test_invalid_parameter_and_input_configurations_are_rejected_before_runtime(self) -> None:
        with self._temporary_root() as root:
            backend = create_backend()
            compiled = self._compile_once(backend, root)
            metadata = backend.load_fmu(compiled.fmu_path)
            cases = (
                (SimulationConfig(parameters={"T": 0.0}), "parameters", "PARAMETER_BELOW_MINIMUM"),
                (SimulationConfig(parameters={"T": "slow"}), "parameters", "INVALID_PARAMETER_TYPE"),
                (SimulationConfig(parameters={"missing": 1.0}), "parameters", "UNKNOWN_PARAMETER"),
                (SimulationConfig(parameters={"u": 1.0}), "parameters", "INVALID_PARAMETER_CAUSALITY"),
                (SimulationConfig(initial_inputs={"T": 0.5}), "initial_inputs", "INVALID_INPUT_CAUSALITY"),
            )
            for config, field, code in cases:
                with self.subTest(field=field, code=code):
                    with self.assertRaises(EngineError) as raised:
                        backend.validate_config(metadata, config)
                    self.assertEqual(ErrorCode.CONFIG_ERROR, raised.exception.code)
                    self.assertIn(
                        (field, code),
                        {
                            (issue["field"], issue["code"])
                            for issue in raised.exception.details["issues"]
                        },
                    )

    def test_input_schedule_switches_after_the_t_1_sample_without_recompilation(self) -> None:
        with self._temporary_root() as root:
            backend = create_backend()
            compiled = self._compile_once(backend, root)
            fmu_sha256 = _sha256(compiled.fmu_path)
            source_sha256 = _sha256(MODEL)
            config = _config(T=0.5, u=1.0, input_schedule=(InputUpdate(1.0, {"u": 2.0}),))

            result = backend.run_fmu(compiled.fmu_path, config)

            _assert_complete_series(self, result)
            self.assertLessEqual(_maximum_scheduled_error(result, T=0.5), 0.03)
            self.assertAlmostEqual(1.0, result.timestamps[100], places=12)
            self.assertLessEqual(
                abs(float(result.outputs["y"][100]) - _scheduled_response(1.0, T=0.5)),
                0.03,
            )
            self.assertGreater(float(result.outputs["y"][101]), float(result.outputs["y"][100]))
            self.assertEqual({"T": 0.5}, config.parameters)
            self.assertEqual(fmu_sha256, _sha256(compiled.fmu_path))
            self.assertEqual(source_sha256, _sha256(MODEL))

            invalid = SimulationConfig(
                start_time=0.0,
                stop_time=2.0,
                communication_step=0.01,
                parameters={"T": 0.5},
                initial_inputs={"u": 1.0},
                input_schedule=(
                    InputUpdate(1.005, {"u": 2.0}),
                    InputUpdate(1.01, {"T": 0.5}),
                ),
            )
            metadata = backend.load_fmu(compiled.fmu_path)
            with self.assertRaises(EngineError) as raised:
                backend.validate_config(metadata, invalid)
            self.assertEqual(ErrorCode.CONFIG_ERROR, raised.exception.code)
            self.assertEqual(
                {"INPUT_TIME_NOT_COMMUNICATION_POINT", "INVALID_INPUT_CAUSALITY"},
                {issue["code"] for issue in raised.exception.details["issues"]},
            )

    @staticmethod
    @contextmanager
    def _temporary_root():
        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT / "artifacts") as directory:
            yield Path(directory)

    def _compile_once(self, backend, root: Path) -> object:
        compiler_path = _omc_path()
        self.assertTrue(compiler_path.is_file(), f"未找到 OMC: {compiler_path}")
        output = root / "output"
        output.mkdir()
        return backend.compile_modelica(
            ModelicaCompileRequest(
                source_path=MODEL,
                model_name="FarcelFirstOrder",
                output_directory=output,
                compiler_path=compiler_path,
            )
        )


def _config(
    *, T: float, u: float, input_schedule: tuple[InputUpdate, ...] = ()
) -> SimulationConfig:
    return SimulationConfig(
        start_time=0.0,
        stop_time=2.0,
        communication_step=0.01,
        parameters={"T": T},
        initial_inputs={"u": u},
        selected_outputs=("y",),
        input_schedule=input_schedule,
    )


def _maximum_error(result, *, T: float, u: float) -> float:
    return max(
        abs(float(value) - u * (1.0 - math.exp(-time / T)))
        for time, value in zip(result.timestamps, result.outputs["y"])
    )


def _assert_complete_series(test: unittest.TestCase, result) -> None:
    expected_samples = 201
    test.assertIs(SimulationState.COMPLETED, result.completion_state)
    test.assertAlmostEqual(0.0, result.start_time, places=12)
    test.assertAlmostEqual(2.0, result.final_time, places=12)
    test.assertAlmostEqual(0.0, result.timestamps[0], places=12)
    test.assertAlmostEqual(2.0, result.timestamps[-1], places=12)
    test.assertEqual(expected_samples, result.sample_count)
    test.assertEqual(expected_samples, len(result.outputs["y"]))
    test.assertTrue(all(math.isfinite(time) for time in result.timestamps))
    test.assertTrue(all(math.isfinite(float(value)) for value in result.outputs["y"]))
    test.assertTrue(all(left < right for left, right in zip(result.timestamps, result.timestamps[1:])))


def _scheduled_response(time: float, *, T: float) -> float:
    value_at_switch = 1.0 - math.exp(-1.0 / T)
    if time <= 1.0:
        return 1.0 - math.exp(-time / T)
    return 2.0 + (value_at_switch - 2.0) * math.exp(-(time - 1.0) / T)


def _maximum_scheduled_error(result, *, T: float) -> float:
    return max(
        abs(float(value) - _scheduled_response(time, T=T))
        for time, value in zip(result.timestamps, result.outputs["y"])
    )


def _omc_path() -> Path:
    return Path(os.environ.get("FARCEL_OPENMODELICA_OMC", DEFAULT_OMC))


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    unittest.main()
