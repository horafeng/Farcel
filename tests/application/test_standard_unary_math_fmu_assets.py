from __future__ import annotations

import hashlib
from importlib import resources
import unittest

from farcel import create_backend
from farcel.application import StandardBlockCatalogLoader, StandardBlockFactory
from farcel.contracts import InterfaceType, SimulationConfig


class StandardUnaryMathFmuAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = StandardBlockCatalogLoader().load()
        self.assets = resources.files("farcel.standard_library").joinpath("assets")

    def test_packaged_unary_math_fmus_match_descriptors_and_sha256(self) -> None:
        for block_id, expected_asset, expected_display_name in (
            ("farcel.math.saturation", "math/saturation/Saturation.fmu", "限幅器"),
            ("farcel.math.abs", "math/abs/Abs.fmu", "绝对值"),
        ):
            with self.subTest(block_id=block_id):
                block = self.catalog.get_block(block_id)
                asset = self.assets.joinpath(block.fmu_asset)

                self.assertEqual(expected_asset, block.fmu_asset)
                self.assertEqual(expected_display_name, block.display_name)
                self.assertEqual(("u",), tuple(port.variable_name for port in block.input_ports))
                self.assertEqual(("y",), tuple(port.variable_name for port in block.output_ports))
                self.assertTrue(asset.is_file())
                self.assertEqual(
                    hashlib.sha256(asset.read_bytes()).hexdigest(),
                    block.fmu_sha256,
                )
                self.assertIs(InterfaceType.CO_SIMULATION, block.execution_interface)

        saturation = self.catalog.get_block("farcel.math.saturation")
        abs_block = self.catalog.get_block("farcel.math.abs")
        self.assertEqual(
            (("lower_limit", -1.0), ("upper_limit", 1.0)),
            tuple((parameter.variable_name, parameter.default_value) for parameter in saturation.parameters),
        )
        self.assertEqual((), abs_block.parameters)

    def test_saturation_and_abs_execute_through_existing_public_backend(self) -> None:
        backend = create_backend()
        cases = (
            ("farcel.math.saturation", {"lower_limit": 0.0, "upper_limit": 3.0}, 5.0, 3.0),
            ("farcel.math.abs", {}, -4.0, 4.0),
        )

        for block_id, parameters, input_value, expected in cases:
            with self.subTest(block_id=block_id):
                block = self.catalog.get_block(block_id)
                with resources.as_file(self.assets.joinpath(block.fmu_asset)) as fmu_path:
                    result = backend.run_fmu(
                        fmu_path,
                        SimulationConfig(
                            stop_time=0.02,
                            communication_step=0.01,
                            parameters=parameters,
                            initial_inputs={"u": input_value},
                            selected_outputs=("y",),
                        ),
                    )

                self.assertEqual((expected, expected, expected), result.outputs["y"])

    def test_parameter_overrides_do_not_mutate_descriptor_and_reach_the_fmu(self) -> None:
        saturation = self.catalog.get_block("farcel.math.saturation")
        factory = StandardBlockFactory(self.catalog)
        node = factory.create_model_node(
            saturation.block_id,
            "saturation",
            parameter_overrides={"lower_limit": 0.0, "upper_limit": 3.0},
        )

        self.assertEqual(
            (("lower_limit", -1.0), ("upper_limit", 1.0)),
            tuple((parameter.variable_name, parameter.default_value) for parameter in saturation.parameters),
        )
        self.assertEqual({"lower_limit": 0.0, "upper_limit": 3.0}, node.config.parameters)

        result = create_backend().run_fmu(
            node.model_path,
            SimulationConfig(
                stop_time=0.02,
                communication_step=0.01,
                parameters=node.config.parameters,
                initial_inputs={"u": 5.0},
                selected_outputs=("y",),
            ),
        )
        self.assertEqual((3.0, 3.0, 3.0), result.outputs["y"])


if __name__ == "__main__":
    unittest.main()
