from __future__ import annotations

import unittest
from pathlib import Path

from farcel.application.graph_runner import GraphSimulationRunner
from farcel.application.graph_runtime_factory import GraphRuntimeBindingsFactory
from farcel.application.graph_validation import GraphValidator
from farcel.application.node_runtime import (
    CoSimulationNodeRuntimeFactory,
    ModelExchangeNodeRuntimeFactory,
)
from farcel.contracts import (
    Connection, GraphSimulationConfig, InterfaceType, ModelNode, ModelNodeConfig,
    PortReference, RunControl, SimulationGraph, SimulationState,
)
from farcel.infrastructure.fmpy import (
    FmpyCvodeSolverFactory, FmpyFmi2ModelExchangeSessionFactory, FmpyImporter,
    FmpySessionFactory,
)


FMUS = Path(__file__).resolve().parents[2] / "examples" / "fmus"
FMI2 = FMUS / "Feedthrough-fmi2.fmu"
FMI3 = FMUS / "Feedthrough-fmi3.fmu"
INPUT = "Float64_continuous_input"
OUTPUT = "Float64_continuous_output"


class GraphMultiFmuIntegrationTests(unittest.TestCase):
    @staticmethod
    def _runtime_factory():
        return GraphRuntimeBindingsFactory(
            FmpyImporter(),
            CoSimulationNodeRuntimeFactory(FmpySessionFactory()),
            ModelExchangeNodeRuntimeFactory(
                FmpyFmi2ModelExchangeSessionFactory(), FmpyCvodeSolverFactory()
            ),
        )

    def _run(self, graph, config, **kwargs):
        report = GraphValidator(FmpyImporter()).validate(graph, config)
        self.assertTrue(report.is_valid, report.issues)
        factory = self._runtime_factory()
        return GraphSimulationRunner(lambda: factory.create(graph, config)).run(
            graph, config, **kwargs
        )

    @staticmethod
    def _node(node_id, path, *, interface, initial=0.0, selected=()):
        return ModelNode(
            node_id,
            str(path),
            ModelNodeConfig(
                initial_inputs={INPUT: initial},
                selected_outputs=selected,
                execution_interface=interface,
            ),
        )

    @staticmethod
    def _edge(source, target):
        return Connection(PortReference(source, OUTPUT), PortReference(target, INPUT))

    @unittest.skipUnless(FMI2.is_file(), "FMI2 Feedthrough is unavailable")
    def test_fmi2_cs_to_cs_routes_unrecorded_source_and_samples_endpoint(self):
        graph = SimulationGraph(
            nodes=(
                self._node("A", FMI2, interface=InterfaceType.CO_SIMULATION, initial=2.0),
                self._node("B", FMI2, interface=InterfaceType.CO_SIMULATION, initial=0.0,
                           selected=(OUTPUT,)),
            ),
            connections=(self._edge("A", "B"),),
        )
        result = self._run(graph, GraphSimulationConfig(stop_time=.03, communication_step=.01,
                                                         output_interval=.02))

        self.assertIs(result.completion_state, SimulationState.COMPLETED)
        self.assertEqual((result.completed_steps, result.final_time, result.timestamps),
                         (3, .03, (0.0, .02, .03)))
        self.assertEqual(result.node_outputs["A"], {})
        self.assertEqual(result.node_outputs["B"][OUTPUT], (0.0, 2.0, 2.0))

    @unittest.skipUnless(FMI3.is_file(), "FMI3 Feedthrough is unavailable")
    def test_fmi3_cs_to_cs_routes_and_keeps_recording_selection_separate(self):
        graph = SimulationGraph(
            nodes=(
                self._node("A", FMI3, interface=InterfaceType.CO_SIMULATION, initial=3.0,
                           selected=("Float64_discrete_output",)),
                self._node("B", FMI3, interface=InterfaceType.CO_SIMULATION, initial=0.0,
                           selected=(OUTPUT,)),
            ),
            connections=(self._edge("A", "B"),),
        )
        result = self._run(graph, GraphSimulationConfig(stop_time=.2, communication_step=.1))

        self.assertEqual(set(result.node_outputs["A"]), {"Float64_discrete_output"})
        self.assertEqual(result.node_outputs["B"][OUTPUT], (0.0, 3.0, 3.0))

    @unittest.skipUnless(FMI2.is_file() and FMI3.is_file(), "Feedthrough fixtures are unavailable")
    def test_mixed_fmi2_fmi3_fmi2_chain_has_one_checkpoint_jacobi_delay(self):
        graph = SimulationGraph(
            nodes=(
                self._node("A", FMI2, interface=InterfaceType.CO_SIMULATION, initial=1.0),
                self._node("B", FMI3, interface=InterfaceType.CO_SIMULATION, initial=2.0,
                           selected=(OUTPUT,)),
                self._node("C", FMI2, interface=InterfaceType.CO_SIMULATION, initial=3.0,
                           selected=(OUTPUT,)),
            ),
            connections=(self._edge("A", "B"), self._edge("B", "C")),
        )
        result = self._run(graph, GraphSimulationConfig(stop_time=.2, communication_step=.1))

        self.assertEqual(result.node_outputs["A"], {})
        self.assertEqual(result.node_outputs["B"][OUTPUT], (2.0, 1.0, 1.0))
        self.assertEqual(result.node_outputs["C"][OUTPUT], (3.0, 2.0, 1.0))

    @unittest.skipUnless(FMI2.is_file(), "FMI2 Feedthrough is unavailable")
    def test_fmi2_cs_me_feedback_uses_existing_model_exchange_input_event_path(self):
        graph = SimulationGraph(
            nodes=(
                self._node("A", FMI2, interface=InterfaceType.CO_SIMULATION, initial=1.0,
                           selected=(OUTPUT,)),
                self._node("B", FMI2, interface=InterfaceType.MODEL_EXCHANGE, initial=4.0,
                           selected=(OUTPUT,)),
            ),
            connections=(self._edge("A", "B"), self._edge("B", "A")),
        )
        result = self._run(graph, GraphSimulationConfig(stop_time=.02, communication_step=.01))

        self.assertEqual(result.node_outputs["A"][OUTPUT], (1.0, 4.0, 1.0))
        self.assertEqual(result.node_outputs["B"][OUTPUT], (4.0, 1.0, 4.0))

    @unittest.skipUnless(FMI2.is_file(), "FMI2 Feedthrough is unavailable")
    def test_repeated_runs_and_initial_progress_stop_leave_graph_reusable(self):
        graph = SimulationGraph(
            nodes=(
                self._node("A", FMI2, interface=InterfaceType.CO_SIMULATION, initial=2.0),
                self._node("B", FMI2, interface=InterfaceType.CO_SIMULATION, selected=(OUTPUT,)),
            ),
            connections=(self._edge("A", "B"),),
        )
        config = GraphSimulationConfig(stop_time=.02, communication_step=.01)
        completed = [self._run(graph, config) for _ in range(3)]
        self.assertTrue(all(item.completion_state is SimulationState.COMPLETED for item in completed))
        self.assertEqual([item.node_outputs["B"][OUTPUT] for item in completed],
                         [(0.0, 2.0, 2.0)] * 3)

        control = RunControl()
        stopped = self._run(graph, config, control=control,
                            on_progress=lambda _: control.request_stop())
        self.assertEqual((stopped.completion_state, stopped.completed_steps,
                          stopped.final_time), (SimulationState.STOPPED, 0, 0.0))
        self.assertIs(self._run(graph, config).completion_state, SimulationState.COMPLETED)
