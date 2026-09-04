from __future__ import annotations
import unittest
from farcel.application.graph_runner import GraphSimulationRunner
from farcel.contracts import Connection, EngineError, ErrorCode, GraphSimulationConfig, ModelNode, ModelNodeConfig, PortReference, RunControl, SimulationGraph, SimulationState

class _Runtime:
    def __init__(self, value, *, control=None, failure=None): self.outputs={"y": value, "recorded": value}; self.inputs=[]; self.control=control; self.failure=failure or {}; self.terminated=self.closed=0
    def initialize(self): self._fail("initialize")
    def set_inputs(self, values): self._fail("input"); self.inputs.append(dict(values))
    def advance_to(self, target):
        self._fail("advance")
        if self.control: self.control.request_stop()
        if self.inputs: self.outputs["y"] = self.inputs[-1].get("u", self.outputs["y"]); self.outputs["recorded"] = self.outputs["y"]
    def read_outputs(self): self._fail("read"); return self.outputs
    def terminate(self): self.terminated+=1; self._fail("terminate")
    def close(self): self.closed+=1; self._fail("close")
    def _fail(self, phase):
        if phase in self.failure: raise self.failure[phase]

class GraphRunnerTests(unittest.TestCase):
    def _graph(self): return SimulationGraph(nodes=(ModelNode("A","a",ModelNodeConfig(selected_outputs=("recorded",))), ModelNode("B","b",ModelNodeConfig(selected_outputs=()))), connections=(Connection(PortReference("A","y"),PortReference("B","u")),))
    def test_normal_sampling_selected_outputs_and_cleanup(self):
        a,b=_Runtime(1),_Runtime(0); calls=[]
        result=GraphSimulationRunner(lambda: calls.append(1) or (("A",a),("B",b))).run(self._graph(),GraphSimulationConfig(stop_time=.03,communication_step=.01,output_interval=.02))
        self.assertEqual((result.timestamps,result.completed_steps,result.completion_state),((0,.02,.03),3,SimulationState.COMPLETED)); self.assertEqual(result.node_outputs["A"]["recorded"],(1,1,1)); self.assertEqual(result.node_outputs["B"],{}); self.assertEqual((a.terminated,a.closed,b.terminated,b.closed),(1,1,1,1))
    def test_prestart_cancel_t0_stop_midstep_stop_and_primary_cleanup(self):
        control=RunControl(); control.request_stop(); calls=[]
        with self.assertRaises(EngineError) as raised: GraphSimulationRunner(lambda: calls.append(1) or ()).run(self._graph(),GraphSimulationConfig(stop_time=.01,communication_step=.01),control=control)
        self.assertIs(raised.exception.code,ErrorCode.CANCELLED); self.assertEqual(calls,[])
        control=RunControl(); a,b=_Runtime(1,control=control),_Runtime(0)
        stopped=GraphSimulationRunner(lambda: (("A",a),("B",b))).run(self._graph(),GraphSimulationConfig(stop_time=.02,communication_step=.01,output_interval=.03),control=control)
        self.assertEqual((stopped.final_time,stopped.completed_steps,stopped.timestamps,stopped.completion_state),(.01,1,(0,.01),SimulationState.STOPPED)); self.assertEqual(len(b.inputs),1)
        a,b=_Runtime(1),_Runtime(0,failure={"input":EngineError(ErrorCode.INPUT_SET_ERROR,"bad")})
        with self.assertRaises(EngineError) as failed: GraphSimulationRunner(lambda: (("A",a),("B",b))).run(self._graph(),GraphSimulationConfig(stop_time=.01,communication_step=.01))
        self.assertIs(failed.exception.code,ErrorCode.INPUT_SET_ERROR); self.assertEqual((a.terminated,a.closed,b.terminated,b.closed),(1,1,1,1))
