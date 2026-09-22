from __future__ import annotations

import hashlib
from importlib import resources
import unittest

from farcel import create_backend
from farcel.application import StandardBlockCatalogLoader
from farcel.contracts import InterfaceType, SimulationConfig


class StandardBlockFmuAssetTests(unittest.TestCase):
    """Verify the first packaged FMUs through metadata and the existing backend."""

    def setUp(self) -> None:
        self.catalog = StandardBlockCatalogLoader().load()
        self.assets = resources.files("farcel.standard_library").joinpath("assets")

    def test_packaged_fmus_exist_and_match_descriptor_sha256(self) -> None:
        for block_id, expected_asset in (
            ("farcel.sources.constant", "sources/constant/Constant.fmu"),
            ("farcel.math.gain", "math/gain/Gain.fmu"),
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

    def test_loader_describes_the_two_fmi2_co_simulation_blocks(self) -> None:
        constant = self.catalog.get_block("farcel.sources.constant")
        gain = self.catalog.get_block("farcel.math.gain")

        self.assertEqual(constant.block_id, "farcel.sources.constant")
        self.assertEqual(gain.block_id, "farcel.math.gain")
        self.assertIs(constant.execution_interface, InterfaceType.CO_SIMULATION)
        self.assertIs(gain.execution_interface, InterfaceType.CO_SIMULATION)

    def test_constant_and_gain_execute_through_existing_public_backend(self) -> None:
        backend = create_backend()
        constant = self.catalog.get_block("farcel.sources.constant")
        gain = self.catalog.get_block("farcel.math.gain")

        with resources.as_file(self.assets.joinpath(constant.fmu_asset)) as constant_path:
            constant_result = backend.run_fmu(
                constant_path,
                SimulationConfig(
                    stop_time=0.02,
                    communication_step=0.01,
                    parameters={"value": 2.5},
                    selected_outputs=("y",),
                ),
            )
        with resources.as_file(self.assets.joinpath(gain.fmu_asset)) as gain_path:
            gain_result = backend.run_fmu(
                gain_path,
                SimulationConfig(
                    stop_time=0.02,
                    communication_step=0.01,
                    parameters={"gain": 3.0},
                    initial_inputs={"u": 4.0},
                    selected_outputs=("y",),
                ),
            )

        self.assertEqual(constant_result.outputs["y"], (2.5, 2.5, 2.5))
        self.assertEqual(gain_result.outputs["y"], (12.0, 12.0, 12.0))
