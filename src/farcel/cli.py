from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from enum import Enum

from farcel.application.engine import FarcelEngine
from farcel.contracts.errors import EngineError
from farcel.contracts.models import ModelMetadata
from farcel.infrastructure.fmpy import FmpyImporter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="farcel", description="Farcel FMU 后端（当前为最小骨架）"
    )
    subparsers = parser.add_subparsers(dest="command")
    inspect_parser = subparsers.add_parser("inspect", help="读取并显示 FMU 元数据")
    inspect_parser.add_argument("fmu", help="要检查的 .fmu 文件")
    inspect_parser.add_argument("--json", action="store_true", help="输出 JSON")
    for command in ("validate", "run", "export"):
        subparsers.add_parser(command, help=f"{command} 命令将在后续 MVP 步骤实现")
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
    parser.error(f"{args.command} 尚未实现；当前版本只提供后端契约和骨架")
    return 2


def _json_default(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"无法序列化 {type(value).__name__}")


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
