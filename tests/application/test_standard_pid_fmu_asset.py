from __future__ import annotations

import hashlib
from importlib import resources
import unittest

from farcel import create_backend
from farcel.application import StandardBlockCatalogLoader, StandardBlockFactory
from farcel.contracts import InterfaceType, ModelNode, SimulationConfig


class StandardPidFmuAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = StandardBlockCatalogLoader().load()
        self.assets = resources.files("farcel.standard_library").joinpath("assets")

    def _run(self, *, parameters: dict[str, float], input_value: float, stop_time: float, step: float):
        block = self.catalog.get_block("farcel.control.pid")
        with resources.as_file(self.assets.joinpath(block.fmu_asset)) as fmu_path:
            return create_backend().run_fmu(
                fmu_path,
                SimulationConfig(
                    stop_time=stop_time,
                    communication_step=step,
                    parameters=parameters,
                    initial_inputs={"u": input_value},
                    selected_outputs=("y",),
                ),
            )

    def test_catalog_loads_pid_descriptor_and_packaged_asset(self) -> None:
        block = self.catalog.get_block("farcel.control.pid")
        asset = self.assets.joinpath(block.fmu_asset)

        self.assertEqual("PID控制器", block.display_name)
        self.assertEqual("control", block.category_id)
        self.assertEqual("control/pid/PID.fmu", block.fmu_asset)
        self.assertEqual(
            ("Kp", "Ki", "Kd", "initial_integral", "initial_error"),
            tuple(parameter.variable_name for parameter in block.parameters),
        )
        self.assertTrue(all(parameter.display_on_block for parameter in block.parameters))
        self.assertEqual(("u",), tuple(port.variable_name for port in block.input_ports))
        self.assertEqual(("y",), tuple(port.variable_name for port in block.output_ports))
        self.assertIs(InterfaceType.CO_SIMULATION, block.execution_interface)
        self.assertTrue(asset.is_file())
        self.assertEqual(hashlib.sha256(asset.read_bytes()).hexdigest(), block.fmu_sha256)

    def test_runtime_proportional_control_output(self) -> None:
        result = self._run(
            parameters={"Kp": 2.0, "Ki": 0.0, "Kd": 0.0},
            input_value=3.0,
            stop_time=0.1,
            step=0.05,
        )

        self.assertEqual(6.0, result.outputs["y"][-1])

    def test_runtime_integral_action_accumulates_for_five_seconds(self) -> None:
        result = self._run(
            parameters={"Kp": 0.0, "Ki": 1.0, "Kd": 0.0},
            input_value=1.0,
            stop_time=5.0,
            step=0.05,
        )

        self.assertAlmostEqual(5.0, result.outputs["y"][-1], delta=1e-9)

    def test_initial_integral_and_initial_error_are_applied(self) -> None:
        integral_result = self._run(
            parameters={
                "Kp": 0.0,
                "Ki": 2.0,
                "Kd": 0.0,
                "initial_integral": 1.5,
            },
            input_value=0.0,
            stop_time=0.1,
            step=0.05,
        )
        derivative_result = self._run(
            parameters={
                "Kp": 0.0,
                "Ki": 0.0,
                "Kd": 1.0,
                "initial_error": 1.0,
            },
            input_value=3.0,
            stop_time=0.1,
            step=0.1,
        )

        self.assertEqual(3.0, integral_result.outputs["y"][0])
        self.assertEqual(20.0, derivative_result.outputs["y"][-1])

    def test_factory_creates_an_existing_model_node(self) -> None:
        block = self.catalog.get_block("farcel.control.pid")
        node = StandardBlockFactory(self.catalog).create_model_node(
            block.block_id,
            "pid",
            parameter_overrides={"Kp": 2.0, "Ki": 1.0, "Kd": 0.5},
        )

        self.assertIsInstance(node, ModelNode)
        self.assertEqual("pid", node.node_id)
        self.assertTrue(node.model_path.endswith("PID.fmu"))
        self.assertEqual({"Kp": 2.0, "Ki": 1.0, "Kd": 0.5}, node.config.parameters)
        self.assertIs(InterfaceType.CO_SIMULATION, node.config.execution_interface)


if __name__ == "__main__":
    unittest.main()
