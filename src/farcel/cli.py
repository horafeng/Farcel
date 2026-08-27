from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from enum import Enum

from farcel.application.engine import FarcelEngine
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import ModelMetadata, SimulationConfig, SimulationResult
from farcel.infrastructure.fmpy import FmpyFmi2SessionFactory, FmpyImporter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="farcel", description="Farcel FMU 后端"
    )
    subparsers = parser.add_subparsers(dest="command")
    inspect_parser = subparsers.add_parser("inspect", help="读取并显示 FMU 元数据")
    inspect_parser.add_argument("fmu", help="要检查的 .fmu 文件")
    inspect_parser.add_argument("--json", action="store_true", help="输出 JSON")
    validate_parser = subparsers.add_parser("validate", help="验证仿真配置")
    validate_parser.add_argument("fmu", help="要验证配置的 .fmu 文件")
    validate_parser.add_argument("--start-time", type=float, default=0.0)
    validate_parser.add_argument("--stop-time", type=float, default=1.0)
    validate_parser.add_argument("--step-size", type=float, default=0.01)
    validate_parser.add_argument(
        "--parameter",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="参数覆盖；VALUE 使用 JSON 标量语法",
    )
    validate_parser.add_argument(
        "--output",
        action="append",
        default=[],
        metavar="NAME",
        help="选择输出变量；可重复指定",
    )
    run_parser = subparsers.add_parser("run", help="执行最小 FMI 2.0 Co-Simulation")
    run_parser.add_argument("fmu", help="要执行的 .fmu 文件")
    run_parser.add_argument("--start-time", type=float, default=0.0)
    run_parser.add_argument("--stop-time", type=float, default=1.0)
    run_parser.add_argument("--step-size", type=float, default=0.01)
    run_parser.add_argument(
        "--parameter",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="参数覆盖；VALUE 使用 JSON 标量语法",
    )
    run_parser.add_argument(
        "--output",
        action="append",
        default=[],
        metavar="NAME",
        help="选择采集的输出变量；可重复指定",
    )
    subparsers.add_parser("export", help="export 命令将在后续 MVP 步骤实现")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "inspect":
        try:
            metadata = FarcelEngine(FmpyImporter()).load_fmu(args.fmu)
        except EngineError as exc:
            print(str(exc), file=sys.stderr)
            for diagnostic in exc.details.get("diagnostics", ()):
                print(f"- {diagnostic}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(asdict(metadata), ensure_ascii=False, indent=2, default=_json_default))
        else:
            _print_metadata(metadata)
        return 0
    if args.command == "validate":
        try:
            engine = FarcelEngine(FmpyImporter())
            metadata = engine.load_fmu(args.fmu)
            config = _build_config(args, selected_outputs=tuple(args.output))
            engine.validate_config(metadata, config)
        except EngineError as exc:
            _print_engine_error(exc)
            return 1
        print("validation successful")
        return 0
    if args.command == "run":
        try:
            engine = FarcelEngine(FmpyImporter(), FmpyFmi2SessionFactory())
            result = engine.run_fmu(
                args.fmu,
                _build_config(args, selected_outputs=tuple(args.output)),
            )
        except EngineError as exc:
            _print_engine_error(exc)
            return 1
        print(f"FMU: {result.fmu_path}")
        print(f"start time: {result.start_time}")
        print(f"stop time: {result.stop_time}")
        print(f"step size: {result.step_size}")
        print(f"completed steps: {result.completed_steps}")
        print(f"samples: {result.sample_count}")
        print(f"final simulation time: {result.final_time}")
        print(f"selected outputs: {', '.join(result.outputs) or 'none'}")
        print(f"execution successful: {'yes' if result.successful else 'no'}")
        _print_sample("first sample", result, 0)
        _print_sample("last sample", result, -1)
        return 0
    parser.error(f"{args.command} 尚未实现；当前版本只提供后端契约和骨架")
    return 2


def _json_default(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"无法序列化 {type(value).__name__}")


def _parse_parameters(items: Sequence[str]) -> dict[str, object]:
    parameters: dict[str, object] = {}
    for item in items:
        name, separator, raw_value = item.partition("=")
        if not separator or not name:
            raise EngineError(
                code=ErrorCode.CONFIG_ERROR,
                message="参数覆盖必须使用 NAME=VALUE 格式",
            )
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            value = raw_value
        parameters[name] = value
    return parameters


def _build_config(
    args: argparse.Namespace, selected_outputs: tuple[str, ...] = ()
) -> SimulationConfig:
    return SimulationConfig(
        start_time=args.start_time,
        stop_time=args.stop_time,
        communication_step=args.step_size,
        parameters=_parse_parameters(args.parameter),
        selected_outputs=selected_outputs,
    )


def _print_engine_error(error: EngineError) -> None:
    print(str(error), file=sys.stderr)
    for issue in error.details.get("issues", ()):
        print(
            f"- [{issue['code']}] {issue['field']}: {issue['message']}",
            file=sys.stderr,
        )
    for diagnostic in error.details.get("diagnostics", ()):
        print(f"- {diagnostic}", file=sys.stderr)
    cleanup_error = error.details.get("cleanup_error")
    if cleanup_error:
        print(
            f"- cleanup [{cleanup_error['code']}]: {cleanup_error['message']}",
            file=sys.stderr,
        )


def _print_sample(label: str, result: SimulationResult, index: int) -> None:
    print(f"{label}:")
    print(f"  time: {result.timestamps[index]}")
    for name, values in result.outputs.items():
        print(f"  {name}: {values[index]}")


def _print_metadata(metadata: ModelMetadata) -> None:
    interfaces = ", ".join(item.value for item in metadata.interface_types)
    executable = (
        metadata.executable_interface.value
        if metadata.executable_interface is not None
        else "否"
    )
    defaults = metadata.default_experiment
    print(f"文件: {metadata.source_path}")
    print("可解析: 是")
    print(f"Farcel 当前可执行: {'是' if metadata.capabilities.can_execute else '否'}")
    print(f"FMI 版本: {metadata.fmi_version}")
    print(f"模型名称: {metadata.model_name}")
    print(f"接口类型: {interfaces}")
    print(f"可执行接口: {executable}")
    print(f"平台: {', '.join(metadata.platforms) or '无'}")
    print(
        "默认实验: "
        f"start={defaults.start_time}, stop={defaults.stop_time}, "
        f"tolerance={defaults.tolerance}, step={defaults.step_size}"
    )
    print(f"变量数量: {len(metadata.variables)}")
    for diagnostic in metadata.diagnostics:
        print(f"诊断: {diagnostic}")


if __name__ == "__main__":
    raise SystemExit(main())
