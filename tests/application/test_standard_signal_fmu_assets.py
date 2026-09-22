from __future__ import annotations

import hashlib
from importlib import resources
import unittest

from farcel import create_backend
from farcel.application import StandardBlockCatalogLoader
from farcel.contracts import InterfaceType, SimulationConfig


class StandardSignalFmuAssetTests(unittest.TestCase):
    """Verify the Step and Sine package assets through the existing backend."""

    def setUp(self) -> None:
        self.catalog = StandardBlockCatalogLoader().load()
        self.assets = resources.files("farcel.standard_library").joinpath("assets")

    def test_packaged_signal_fmus_exist_and_match_descriptor_sha256(self) -> None:
        for block_id, expected_asset in (
            ("farcel.sources.step", "sources/step/Step.fmu"),
            ("farcel.sources.sine", "sources/sine/Sine.fmu"),
        ):
            with self.subTest(block_id=block_id):
                block = self.catalog.get_block(block_id)
                asset = self.assets.joinpath(block.fmu_asset)

                self.assertEqual(block.fmu_asset, expected_asset)
                self.assertTrue(asset.is_file())
                self.assertEqual(
                    hashlib.sha256(asset.read_bytes()).hexdigest(),
                    block.fmu_sha256,
                )
                self.assertIs(block.execution_interface, InterfaceType.CO_SIMULATION)

    def test_loader_preserves_chinese_signal_metadata(self) -> None:
        step = self.catalog.get_block("farcel.sources.step")
        sine = self.catalog.get_block("farcel.sources.sine")

        self.assertEqual(step.display_name, "阶跃")
        self.assertEqual(
            tuple(parameter.display_name for parameter in step.parameters),
            ("初始值", "终值", "阶跃时间"),
        )
        self.assertEqual(sine.display_name, "正弦信号")
        self.assertEqual(
            tuple(parameter.display_name for parameter in sine.parameters),
            ("幅值", "频率", "相位", "偏置"),
        )

    def test_step_and_sine_execute_through_existing_public_backend(self) -> None:
        backend = create_backend()
        step = self.catalog.get_block("farcel.sources.step")
        sine = self.catalog.get_block("farcel.sources.sine")

        with resources.as_file(self.assets.joinpath(step.fmu_asset)) as step_path:
            step_result = backend.run_fmu(
                step_path,
                SimulationConfig(
                    stop_time=0.02,
                    communication_step=0.01,
                    parameters={
                        "initial_value": 0.0,
                        "final_value": 5.0,
                        "step_time": 0.01,
                    },
                    selected_outputs=("y",),
                ),
            )
        with resources.as_file(self.assets.joinpath(sine.fmu_asset)) as sine_path:
            sine_result = backend.run_fmu(
                sine_path,
                SimulationConfig(
                    stop_time=0.02,
                    communication_step=0.01,
                    parameters={
                        "amplitude": 2.0,
                        "frequency_hz": 1.0,
                        "phase_rad": 0.0,
                        "offset": 0.0,
                    },
                    selected_outputs=("y",),
                ),
            )

        self.assertEqual(step_result.outputs["y"], (0.0, 5.0, 5.0))
        self.assertNotEqual(sine_result.outputs["y"][0], sine_result.outputs["y"][1])
        self.assertNotEqual(sine_result.outputs["y"][1], sine_result.outputs["y"][2])
