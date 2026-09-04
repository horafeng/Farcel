import unittest

from farcel.application.graph_validation import GraphValidator
from farcel.contracts import (
    Connection,
    GraphSimulationConfig,
    InputUpdate,
    InterfaceCapability,
    InterfaceType,
    ModelMetadata,
    ModelNode,
    ModelNodeConfig,
    PortReference,
    SimulationGraph,
    VariableMetadata,
)
from farcel.contracts.errors import EngineError, ErrorCode


class _Importer:
    def __init__(self, models: dict[str, ModelMetadata | BaseException]) -> None:
        self.models = models
        self.loaded: list[str] = []

    def load(self, path):
        path_string = str(path)
        self.loaded.append(path_string)
        model = self.models[path_string]
        if isinstance(model, BaseException):
            raise model
        return model


def _metadata(
    name: str,
    variables: tuple[VariableMetadata, ...],
    *,
    fmi_version: str = "2.0",
    interfaces: tuple[InterfaceType, ...] = (InterfaceType.CO_SIMULATION,),
    capabilities: tuple[InterfaceCapability, ...] | None = None,
) -> ModelMetadata:
    if capabilities is None:
        capabilities = tuple(
            InterfaceCapability(interface, can_execute=True) for interface in interfaces
        )
    executable = next(
        (
            capability.interface_type
            for capability in capabilities
            if capability.can_execute
            and capability.interface_type in {
                InterfaceType.CO_SIMULATION,
                InterfaceType.MODEL_EXCHANGE,
            }
        ),
        None,
    )
    return ModelMetadata(
        model_id=name,
        source_path=f"{name}.fmu",
        fmi_version=fmi_version,
        model_name=name,
        interface_types=interfaces,
        executable_interface=executable,
        interface_capabilities=capabilities,
        variables=variables,
    )


def _variable(
    name: str,
    data_type: str,
    causality: str,
    *,
    shape: tuple[int, ...] = (),
    value_reference: int = 1,
    dimensions: tuple[int | None, ...] = (),
    start: object = None,
) -> VariableMetadata:
    return VariableMetadata(
        name,
        value_reference,
        data_type,
        causality=causality,
        shape=shape,
        dimension_value_references=dimensions,
        start=start,
    )


def _node(name: str, config: ModelNodeConfig | None = None) -> ModelNode:
    return ModelNode(name, f"{name}.fmu", config or ModelNodeConfig())


def _connection(source: str, target: str, *, source_var: str = "y", target_var: str = "u") -> Connection:
    return Connection(PortReference(source, source_var), PortReference(target, target_var))


class GraphValidationTests(unittest.TestCase):
    def test_valid_two_node_graph_and_unselected_source_output(self) -> None:
        source = _metadata("source", (_variable("y", "Real", "output"),))
        target = _metadata("target", (_variable("u", "Real", "input"),))
        importer = _Importer({"source.fmu": source, "target.fmu": target})
        graph = SimulationGraph(nodes=(_node("source"), _node("target")), connections=(_connection("source", "target"),))

        report = GraphValidator(importer).validate(graph, GraphSimulationConfig())

        self.assertTrue(report.is_valid)
        self.assertEqual(importer.loaded, ["source.fmu", "target.fmu"])

    def test_global_timing_reuses_single_model_codes_and_adds_duration_rule(self) -> None:
        validator = GraphValidator(_Importer({}))
        valid = validator.validate(
            SimulationGraph(),
            GraphSimulationConfig(stop_time=0.3, communication_step=0.1),
        )
        invalid = validator.validate(
            SimulationGraph(),
            GraphSimulationConfig(stop_time=1.0, communication_step=0.3, output_interval=0.2),
        )

        self.assertIn("GRAPH_DURATION_NOT_COMMUNICATION_ALIGNED", {issue.code for issue in invalid.issues})
        self.assertIn("OUTPUT_INTERVAL_NOT_COMMUNICATION_ALIGNED", {issue.code for issue in invalid.issues})
        self.assertNotIn("GRAPH_DURATION_NOT_COMMUNICATION_ALIGNED", {issue.code for issue in valid.issues})

    def test_global_timing_rejects_schema_non_finite_and_invalid_ranges(self) -> None:
        report = GraphValidator(_Importer({})).validate(
            SimulationGraph(),
            GraphSimulationConfig(
                schema_version="2.0",
                start_time=float("nan"),
                stop_time=0.0,
                communication_step=0.0,
                output_interval=float("inf"),
            ),
        )

        self.assertEqual(
            [issue.code for issue in report.issues],
            [
                "UNSUPPORTED_SCHEMA",
                "INVALID_TIME_VALUE",
                "INVALID_STEP_SIZE",
                "INVALID_OUTPUT_INTERVAL",
                "EMPTY_GRAPH",
            ],
        )

    def test_node_identity_and_empty_path_are_reported_before_import(self) -> None:
        model = _metadata("model", ())
        importer = _Importer({"first.fmu": model, "second.fmu": model})
        graph = SimulationGraph(
            nodes=(
                ModelNode(" ", " "),
                ModelNode("same", "first.fmu"),
                ModelNode("same", "second.fmu"),
            )
        )

        report = GraphValidator(importer).validate(graph, GraphSimulationConfig())

        self.assertEqual(importer.loaded, ["first.fmu", "second.fmu"])
        self.assertEqual(
            [(issue.field, issue.code) for issue in report.issues],
            [
                ("nodes[0].node_id", "EMPTY_NODE_ID"),
                ("nodes[0].model_path", "EMPTY_MODEL_PATH"),
                ("nodes[2].node_id", "DUPLICATE_NODE_ID"),
            ],
        )

    def test_same_model_path_is_valid_for_two_distinct_nodes(self) -> None:
        model = _metadata("shared", (_variable("y", "Real", "output"), _variable("u", "Real", "input", value_reference=2)))
        importer = _Importer({"shared.fmu": model})
        graph = SimulationGraph(
            nodes=(ModelNode("left", "shared.fmu"), ModelNode("right", "shared.fmu")),
            connections=(_connection("left", "right"),),
        )

        report = GraphValidator(importer).validate(graph, GraphSimulationConfig())

        self.assertTrue(report.is_valid)
        self.assertEqual(importer.loaded, ["shared.fmu", "shared.fmu"])

    def test_importer_engine_error_maps_to_node_model_path(self) -> None:
        importer = _Importer({"bad.fmu": EngineError(ErrorCode.IMPORT_ERROR, "cannot import")})

        report = GraphValidator(importer).validate(
            SimulationGraph(nodes=(ModelNode("bad", "bad.fmu"),)), GraphSimulationConfig()
        )

        self.assertEqual(
            [(issue.field, issue.code, issue.message) for issue in report.issues],
            [("nodes[0].model_path", "IMPORT_ERROR", "cannot import")],
        )

    def test_connection_endpoint_and_causality_issues(self) -> None:
        source = _metadata("source", (_variable("not_output", "Real", "input"),))
        target = _metadata("target", (_variable("not_input", "Real", "output"),))
        validator = GraphValidator(_Importer({"source.fmu": source, "target.fmu": target}))
        graph = SimulationGraph(
            nodes=(_node("source"), _node("target")),
            connections=(
                _connection("missing", "target"),
                _connection("source", "missing"),
                _connection("source", "target", source_var="missing"),
                _connection("source", "target", target_var="missing"),
                _connection("source", "target", source_var="not_output", target_var="not_input"),
            ),
        )

        report = validator.validate(graph, GraphSimulationConfig())

        self.assertEqual(
            [issue.code for issue in report.issues],
            [
                "UNKNOWN_SOURCE_NODE",
                "UNKNOWN_TARGET_NODE",
                "UNKNOWN_SOURCE_VARIABLE",
                "UNKNOWN_TARGET_VARIABLE",
                "UNKNOWN_SOURCE_VARIABLE",
                "UNKNOWN_TARGET_VARIABLE",
                "INVALID_SOURCE_CAUSALITY",
                "INVALID_TARGET_CAUSALITY",
            ],
        )

    def test_duplicate_connections_and_single_driver_rule(self) -> None:
        source = _metadata("source", (_variable("y", "Real", "output"),))
        other = _metadata("other", (_variable("y", "Real", "output"),))
        target = _metadata("target", (_variable("u", "Real", "input"),))
        graph = SimulationGraph(
            nodes=(_node("source"), _node("other"), _node("target")),
            connections=(
                _connection("source", "target"),
                _connection("source", "target"),
                _connection("other", "target"),
            ),
        )
        importer = _Importer({"source.fmu": source, "other.fmu": other, "target.fmu": target})

        report = GraphValidator(importer).validate(graph, GraphSimulationConfig())

        self.assertEqual([issue.code for issue in report.issues], ["DUPLICATE_CONNECTION", "MULTIPLE_INPUT_DRIVERS"])

    def test_fan_out_initial_inputs_feedback_and_self_loop_are_legal(self) -> None:
        model = _metadata(
            "model",
            (
                _variable("y", "Real", "output"),
                _variable("u", "Real", "input", value_reference=2),
                _variable("v", "Real", "input", value_reference=3),
            ),
        )
        importer = _Importer({"a.fmu": model, "b.fmu": model, "c.fmu": model})
        graph = SimulationGraph(
            nodes=(
                _node("a", ModelNodeConfig(initial_inputs={"u": 1.0})),
                _node("b"),
                _node("c"),
            ),
            connections=(
                _connection("a", "b"),
                _connection("a", "c"),
                _connection("b", "a"),
                _connection("c", "c", target_var="v"),
            ),
        )

        report = GraphValidator(importer).validate(graph, GraphSimulationConfig())

        self.assertTrue(report.is_valid)

    def test_input_schedule_connection_conflict_is_rejected(self) -> None:
        source = _metadata("source", (_variable("y", "Real", "output"),))
        target = _metadata("target", (_variable("u", "Real", "input"),))
        graph = SimulationGraph(
            nodes=(
                _node("source"),
                _node("target", ModelNodeConfig(input_schedule=(InputUpdate(0.1, {"u": 1.0}),))),
            ),
            connections=(_connection("source", "target"),),
        )

        report = GraphValidator(_Importer({"source.fmu": source, "target.fmu": target})).validate(graph, GraphSimulationConfig())

        self.assertEqual([issue.code for issue in report.issues], ["INPUT_SCHEDULE_CONNECTION_CONFLICT"])

    def test_type_compatibility_matrix(self) -> None:
        supported = (
            ("Real", "2.0", "Float64", "3.0"),
            ("Integer", "2.0", "Int32", "3.0"),
            ("Boolean", "2.0", "Boolean", "3.0"),
            ("String", "2.0", "String", "3.0"),
            ("Enumeration", "2.0", "Enumeration", "3.0"),
            ("Float32", "3.0", "Float32", "3.0"),
            ("UInt64", "3.0", "UInt64", "3.0"),
        )
        for source_type, source_version, target_type, target_version in supported:
            with self.subTest(source_type=source_type, target_type=target_type):
                source = _metadata("source", (_variable("y", source_type, "output"),), fmi_version=source_version)
                target = _metadata("target", (_variable("u", target_type, "input"),), fmi_version=target_version)
                report = GraphValidator(_Importer({"source.fmu": source, "target.fmu": target})).validate(
                    SimulationGraph(nodes=(_node("source"), _node("target")), connections=(_connection("source", "target"),)),
                    GraphSimulationConfig(),
                )
                self.assertTrue(report.is_valid)

    def test_incompatible_and_unsupported_connection_types_are_rejected(self) -> None:
        cases = (
            ("Float64", "Float32", "INCOMPATIBLE_CONNECTION_TYPE"),
            ("Int64", "Int8", "INCOMPATIBLE_CONNECTION_TYPE"),
            ("Binary", "Binary", "UNSUPPORTED_CONNECTION_TYPE"),
            ("Clock", "Clock", "UNSUPPORTED_CONNECTION_TYPE"),
        )
        for source_type, target_type, expected in cases:
            with self.subTest(source_type=source_type, target_type=target_type):
                source = _metadata("source", (_variable("y", source_type, "output"),), fmi_version="3.0")
                target = _metadata("target", (_variable("u", target_type, "input"),), fmi_version="3.0")
                report = GraphValidator(_Importer({"source.fmu": source, "target.fmu": target})).validate(
                    SimulationGraph(nodes=(_node("source"), _node("target")), connections=(_connection("source", "target"),)),
                    GraphSimulationConfig(),
                )
                self.assertEqual([issue.code for issue in report.issues], [expected])

    def test_static_array_shapes_must_match_exactly(self) -> None:
        cases = (((3,), (3,), True), ((), (1,), False), ((3,), (4,), False), ((2, 2), (4,), False))
        for source_shape, target_shape, valid in cases:
            with self.subTest(source_shape=source_shape, target_shape=target_shape):
                source = _metadata("source", (_variable("y", "Float64", "output", shape=source_shape),), fmi_version="3.0")
                target = _metadata("target", (_variable("u", "Float64", "input", shape=target_shape),), fmi_version="3.0")
                report = GraphValidator(_Importer({"source.fmu": source, "target.fmu": target})).validate(
                    SimulationGraph(nodes=(_node("source"), _node("target")), connections=(_connection("source", "target"),)),
                    GraphSimulationConfig(),
                )
                self.assertEqual(report.is_valid, valid)
                if not valid:
                    self.assertEqual([issue.code for issue in report.issues], ["INCOMPATIBLE_CONNECTION_SHAPE"])

    def test_dynamic_array_shapes_reuse_effective_shape_resolution(self) -> None:
        def dynamic_model(name: str) -> ModelMetadata:
            return _metadata(
                name,
                (
                    _variable("n", "UInt64", "structuralParameter", value_reference=1, start=3),
                    _variable("port", "Float64", "output" if name == "source" else "input", shape=(3,), value_reference=2, dimensions=(1,)),
                ),
                fmi_version="3.0",
            )

        source = dynamic_model("source")
        target = dynamic_model("target")
        valid_graph = SimulationGraph(
            nodes=(
                _node("source", ModelNodeConfig(parameters={"n": 2})),
                _node("target", ModelNodeConfig(parameters={"n": 2})),
            ),
            connections=(_connection("source", "target", source_var="port", target_var="port"),),
        )
        mismatch_graph = SimulationGraph(
            nodes=(
                _node("source", ModelNodeConfig(parameters={"n": 2})),
                _node("target"),
            ),
            connections=(_connection("source", "target", source_var="port", target_var="port"),),
        )
        importer = _Importer({"source.fmu": source, "target.fmu": target})
        validator = GraphValidator(importer)

        self.assertTrue(validator.validate(valid_graph, GraphSimulationConfig()).is_valid)
        mismatch = validator.validate(mismatch_graph, GraphSimulationConfig())
        self.assertEqual([issue.code for issue in mismatch.issues], ["INCOMPATIBLE_CONNECTION_SHAPE"])

    def test_node_config_issues_reuse_single_model_validation_with_prefixes(self) -> None:
        model = _metadata(
            "node",
            (
                _variable("gain", "Real", "parameter"),
                _variable("u", "Real", "input", value_reference=2),
            ),
        )
        graph = SimulationGraph(
            nodes=(
                _node(
                    "node",
                    ModelNodeConfig(
                        parameters={"missing": 1.0},
                        input_schedule=(InputUpdate(0.015, {"u": 1.0}),),
                    ),
                ),
            )
        )

        report = GraphValidator(_Importer({"node.fmu": model})).validate(graph, GraphSimulationConfig())

        self.assertEqual(
            [(issue.field, issue.code) for issue in report.issues],
            [
                ("nodes[0].config.parameters", "UNKNOWN_PARAMETER"),
                ("nodes[0].config.input_schedule[0]", "INPUT_TIME_NOT_COMMUNICATION_POINT"),
            ],
        )

    def test_execution_capability_reuses_existing_phase_3_policy(self) -> None:
        no_binary = _metadata(
            "no_binary",
            (),
            capabilities=(InterfaceCapability(InterfaceType.CO_SIMULATION, can_execute=False),),
        )
        fmi3_me = _metadata(
            "fmi3_me",
            (),
            fmi_version="3.0",
            interfaces=(InterfaceType.MODEL_EXCHANGE,),
            capabilities=(InterfaceCapability(InterfaceType.MODEL_EXCHANGE, can_execute=True),),
        )
        dual = _metadata(
            "dual",
            (),
            interfaces=(InterfaceType.CO_SIMULATION, InterfaceType.MODEL_EXCHANGE),
            capabilities=(
                InterfaceCapability(InterfaceType.CO_SIMULATION, can_execute=True),
                InterfaceCapability(InterfaceType.MODEL_EXCHANGE, can_execute=True),
            ),
        )
        scheduled = _metadata(
            "scheduled",
            (),
            interfaces=(InterfaceType.SCHEDULED_EXECUTION,),
            capabilities=(InterfaceCapability(InterfaceType.SCHEDULED_EXECUTION, can_execute=True),),
        )
        importer = _Importer({
            "no_binary.fmu": no_binary,
            "fmi3_me.fmu": fmi3_me,
            "dual.fmu": dual,
            "scheduled.fmu": scheduled,
        })
        graph = SimulationGraph(
            nodes=(
                _node("no_binary"),
                _node("fmi3_me", ModelNodeConfig(execution_interface=InterfaceType.MODEL_EXCHANGE)),
                _node("dual"),
                _node("scheduled", ModelNodeConfig(execution_interface=InterfaceType.SCHEDULED_EXECUTION)),
            )
        )

        report = GraphValidator(importer).validate(graph, GraphSimulationConfig())

        self.assertEqual(
            [(issue.field, issue.code) for issue in report.issues],
            [
                ("nodes[0].model_path", "PLATFORM_BINARY_MISSING"),
                ("nodes[1].config.execution_interface", "UNSUPPORTED_INTERFACE"),
                ("nodes[3].config.execution_interface", "UNSUPPORTED_INTERFACE"),
            ],
        )

    def test_issue_order_is_deterministic_and_validator_only_loads_metadata(self) -> None:
        model = _metadata("node", (_variable("y", "Real", "output"),))
        importer = _Importer({"node.fmu": model})
        graph = SimulationGraph(
            nodes=(ModelNode("", "node.fmu"),),
            connections=(_connection("unknown", "unknown"),),
        )
        validator = GraphValidator(importer)
        config = GraphSimulationConfig(stop_time=1.0, communication_step=0.3)

        first = validator.validate(graph, config)
        second = validator.validate(graph, config)

        self.assertEqual(first.issues, second.issues)
        self.assertEqual(
            [issue.code for issue in first.issues],
            ["GRAPH_DURATION_NOT_COMMUNICATION_ALIGNED", "EMPTY_NODE_ID", "UNKNOWN_SOURCE_NODE", "UNKNOWN_TARGET_NODE"],
        )
        self.assertEqual(importer.loaded, ["node.fmu", "node.fmu"])
