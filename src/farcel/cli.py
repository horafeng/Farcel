from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from enum import Enum
from pathlib import Path

from farcel import create_backend
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.modelica import (
    ModelicaCompileRequest,
    ModelicaEnvironmentInfo,
)
from farcel.contracts.modelica_provenance import (
    ModelicaAssetStagingRequest,
    ModelicaBuildProvenance,
    ProjectModelicaCompilation,
)
from farcel.contracts.project import ModelAsset, ProjectSchemaMigrationResult, SimulationProject
from farcel.contracts.models import (
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
    SimulationResult,
)


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
    _add_execution_interface_argument(validate_parser)
    validate_parser.add_argument(
        "--output-interval",
        type=float,
        default=None,
        help="结果采样间隔；省略时等于 --step-size",
    )
    validate_parser.add_argument(
        "--parameter",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="参数覆盖；VALUE 使用 JSON 标量语法",
    )
    validate_parser.add_argument(
        "--input", action="append", default=[], metavar="NAME=VALUE",
        help="初始 input；VALUE 使用 JSON 标量语法",
    )
    validate_parser.add_argument(
        "--output",
        action="append",
        default=[],
        metavar="NAME",
        help="选择输出变量；可重复指定",
    )
    run_parser = subparsers.add_parser("run", help="执行 Farcel 当前支持的单 FMU 接口")
    run_parser.add_argument("fmu", help="要执行的 .fmu 文件")
    run_parser.add_argument("--start-time", type=float, default=0.0)
    run_parser.add_argument("--stop-time", type=float, default=1.0)
    run_parser.add_argument("--step-size", type=float, default=0.01)
    _add_execution_interface_argument(run_parser)
    run_parser.add_argument(
        "--output-interval",
        type=float,
        default=None,
        help="结果采样间隔；省略时等于 --step-size",
    )
    run_parser.add_argument(
        "--parameter",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="参数覆盖；VALUE 使用 JSON 标量语法",
    )
    run_parser.add_argument(
        "--input", action="append", default=[], metavar="NAME=VALUE",
        help="初始 input；VALUE 使用 JSON 标量语法",
    )
    run_parser.add_argument(
        "--output",
        action="append",
        default=[],
        metavar="NAME",
        help="选择采集的输出变量；可重复指定",
    )
    export_parser = subparsers.add_parser("export", help="执行仿真并导出 CSV")
    export_parser.add_argument("fmu", help="要执行的 .fmu 文件")
    export_parser.add_argument("--start-time", type=float, default=0.0)
    export_parser.add_argument("--stop-time", type=float, default=1.0)
    export_parser.add_argument("--step-size", type=float, default=0.01)
    _add_execution_interface_argument(export_parser)
    export_parser.add_argument(
        "--output-interval",
        type=float,
        default=None,
        help="结果采样间隔；省略时等于 --step-size",
    )
    export_parser.add_argument(
        "--parameter",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="参数覆盖；VALUE 使用 JSON 标量语法",
    )
    export_parser.add_argument(
        "--input", action="append", default=[], metavar="NAME=VALUE",
        help="初始 input；VALUE 使用 JSON 标量语法",
    )
    export_parser.add_argument(
        "--output",
        action="append",
        default=[],
        metavar="NAME",
        help="选择导出的输出变量；可重复指定",
    )
    export_parser.add_argument(
        "--csv",
        required=True,
        metavar="PATH",
        help="CSV 目标文件路径",
    )
    modelica_parser = subparsers.add_parser("modelica", help="查询或编译 Modelica 模型")
    modelica_subparsers = modelica_parser.add_subparsers(dest="modelica_command")
    modelica_env_parser = modelica_subparsers.add_parser(
        "env", help="检查 OpenModelica 编译器环境"
    )
    modelica_env_parser.add_argument("--omc", metavar="PATH", help="显式 OMC 可执行文件路径")
    modelica_env_parser.add_argument("--json", action="store_true", help="输出 JSON")
    modelica_compile_parser = modelica_subparsers.add_parser(
        "compile", help="将单个 .mo 模型编译为 FMI 2.0 Co-Simulation FMU"
    )
    modelica_compile_parser.add_argument("source_mo", metavar="SOURCE_MO", help="Modelica 源文件")
    modelica_compile_parser.add_argument("--model", required=True, metavar="NAME", help="要编译的模型类名")
    modelica_compile_parser.add_argument(
        "--output-dir", required=True, metavar="PATH", help="已存在的 FMU 输出目录"
    )
    modelica_compile_parser.add_argument("--omc", metavar="PATH", help="显式 OMC 可执行文件路径")
    modelica_compile_parser.add_argument(
        "--timeout", type=float, default=120.0, metavar="SECONDS", help="编译超时秒数，默认 120"
    )
    modelica_compile_parser.add_argument("--json", action="store_true", help="输出 JSON")
    modelica_project_parser = modelica_subparsers.add_parser(
        "project", help="迁移、编译登记或查询 Modelica Project 构建记录"
    )
    modelica_project_subparsers = modelica_project_parser.add_subparsers(
        dest="modelica_project_command"
    )
    modelica_project_migrate_parser = modelica_project_subparsers.add_parser(
        "migrate", help="显式将 Project schema 1.0 迁移到 1.1"
    )
    _add_project_root_argument(modelica_project_migrate_parser)
    modelica_project_migrate_parser.add_argument("--json", action="store_true", help="输出 JSON")
    modelica_project_compile_parser = modelica_project_subparsers.add_parser(
        "compile", help="编译 Modelica 模型并登记到 schema 1.1 Project"
    )
    _add_project_root_argument(modelica_project_compile_parser)
    modelica_project_compile_parser.add_argument("--source", required=True, metavar="PATH", help="Modelica 源文件")
    modelica_project_compile_parser.add_argument("--model", required=True, metavar="NAME", help="要编译的模型类名")
    modelica_project_compile_parser.add_argument("--omc", metavar="PATH", help="显式 OMC 可执行文件路径")
    modelica_project_compile_parser.add_argument(
        "--timeout", type=float, default=120.0, metavar="SECONDS", help="编译超时秒数，默认 120"
    )
    modelica_project_compile_parser.add_argument("--json", action="store_true", help="输出 JSON")
    modelica_project_list_parser = modelica_project_subparsers.add_parser(
        "list", help="列出 Project 已登记的 Modelica 构建来源"
    )
    _add_project_root_argument(modelica_project_list_parser)
    modelica_project_list_parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "modelica":
        if args.modelica_command is None:
            parser.parse_args(["modelica", "--help"])
            return 2
        if args.modelica_command == "env":
            return _modelica_env(args)
        if args.modelica_command == "compile":
            return _modelica_compile(args)
        if args.modelica_command == "project":
            if args.modelica_project_command is None:
                parser.parse_args(["modelica", "project", "--help"])
                return 2
            if args.modelica_project_command == "migrate":
                return _modelica_project_migrate(args)
            if args.modelica_project_command == "compile":
                return _modelica_project_compile(args)
            if args.modelica_project_command == "list":
                return _modelica_project_list(args)
    if args.command == "inspect":
        try:
            metadata = create_backend().load_fmu(args.fmu)
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
            engine = create_backend()
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
            engine = create_backend()
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
    if args.command == "export":
        try:
            engine = create_backend()
            result = engine.run_fmu(
                args.fmu,
                _build_config(args, selected_outputs=tuple(args.output)),
            )
            report = engine.export_result(result, args.csv)
        except EngineError as exc:
            _print_engine_error(exc)
            return 1
        print(f"CSV: {report.destination}")
        print(f"data rows: {report.row_count}")
        print("export successful")
        return 0
    parser.error(f"{args.command} 尚未实现；当前版本只提供后端契约和骨架")
    return 2


def _json_default(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"无法序列化 {type(value).__name__}")


def _modelica_env(args: argparse.Namespace) -> int:
    """Delegate compiler discovery to the public backend facade."""

    try:
        info = create_backend().get_modelica_environment(compiler_path=args.omc)
    except EngineError as exc:
        return _print_modelica_error(exc, as_json=args.json)
    payload = _modelica_environment_payload(info)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, default=_json_default))
    else:
        print(f"状态: {payload['status']}")
        print(f"编译器: {info.compiler_name}")
        print(f"版本: {info.compiler_version or '未知'}")
        print(f"诊断: {info.diagnostic_message}")
    return 0 if info.is_available else 1


def _modelica_compile(args: argparse.Namespace) -> int:
    """Construct the public request contract and delegate standalone compilation."""

    request = ModelicaCompileRequest(
        source_path=args.source_mo,
        model_name=args.model,
        output_directory=args.output_dir,
        compiler_path=args.omc,
        timeout_seconds=args.timeout,
    )
    try:
        result = create_backend().compile_modelica(request)
    except EngineError as exc:
        return _print_modelica_error(exc, as_json=args.json)
    payload = {
        "success": True,
        "fmu_path": str(result.fmu_path),
        "fmi_version": result.fmi_version,
        "fmu_type": result.fmu_type,
        "compiler_version": result.compiler_version,
        "sha256": result.sha256,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, default=_json_default))
    else:
        print(f"FMU: {payload['fmu_path']}")
        print(f"FMI 版本: {payload['fmi_version']}")
        print(f"FMU 类型: {payload['fmu_type']}")
        print(f"OpenModelica 版本: {payload['compiler_version']}")
        print(f"SHA-256: {payload['sha256']}")
        print("编译完成: 是")
    return 0


def _modelica_project_migrate(args: argparse.Namespace) -> int:
    """Explicitly delegate schema migration to the public backend facade."""

    try:
        result = create_backend().migrate_project_schema(args.project_root)
    except EngineError as exc:
        return _print_modelica_error(exc, as_json=args.json)
    payload = _project_migration_payload(args.project_root, result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, default=_json_default))
    else:
        print(f"项目目录: {payload['project_root']}")
        print(f"Schema 版本: {payload['schema_version']}")
        print(f"备份文件: {payload['backup_relative_path']}")
        print("迁移完成: 是")
    return 0


def _modelica_project_compile(args: argparse.Namespace) -> int:
    """Compile and register a build only through the public Project API."""

    backend = create_backend()
    try:
        project = backend.open_project(args.project_root)
        request = ModelicaAssetStagingRequest(
            project_root=args.project_root,
            source_path=args.source,
            model_name=args.model,
            compiler_path=args.omc,
            timeout_seconds=args.timeout,
        )
        registration = backend.compile_project_modelica(project, request)
    except EngineError as exc:
        return _print_modelica_error(exc, as_json=args.json)
    payload = _project_compilation_payload(registration)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, default=_json_default))
    else:
        print(f"项目 ID: {payload['project_id']}")
        print(f"Schema 版本: {payload['schema_version']}")
        print(f"构建 ID: {payload['build_id']}")
        print(f"FMU 资产: {payload['fmu_relative_path']}")
        print(f"FMU SHA-256: {payload['fmu_sha256']}")
        print(f"源快照: {payload['source_relative_path']}")
        print(f"源 SHA-256: {payload['source_sha256']}")
        print("编译并登记完成: 是")
    return 0


def _modelica_project_list(args: argparse.Namespace) -> int:
    """List persisted provenance only; no compilation or filesystem mutation."""

    backend = create_backend()
    try:
        project = backend.open_project(args.project_root)
        provenances = backend.list_project_modelica_builds(project)
    except EngineError as exc:
        return _print_modelica_error(exc, as_json=args.json)
    payload = _project_build_list_payload(project, provenances)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, default=_json_default))
    else:
        print(f"项目 ID: {payload['project_id']}")
        print(f"Schema 版本: {payload['schema_version']}")
        print(f"已登记 Modelica 构建数: {payload['count']}")
        for build in payload["builds"]:
            print(f"- {build['build_id']}: {build['model_name']} -> {build['fmu_relative_path']}")
    return 0


def _project_migration_payload(
    project_root: str | Path,
    result: ProjectSchemaMigrationResult,
) -> dict[str, object]:
    return {
        "success": True,
        "project_root": str(project_root),
        "schema_version": result.project.schema_version,
        "backup_relative_path": result.backup_relative_path,
    }


def _project_compilation_payload(
    registration: ProjectModelicaCompilation,
) -> dict[str, object]:
    staged = registration.staged_build
    provenance = staged.provenance
    asset = staged.model_asset
    return {
        "success": True,
        "project_id": registration.project.project_id,
        "schema_version": registration.project.schema_version,
        "build_id": provenance.build_id,
        "asset_id": asset.asset_id,
        "fmu_relative_path": asset.relative_path,
        "fmu_sha256": asset.sha256,
        "source_relative_path": provenance.source_snapshot.relative_path,
        "source_sha256": provenance.source_snapshot.sha256,
        "model_name": provenance.model_name,
        "compiler_name": provenance.compiler_name,
        "compiler_version": provenance.compiler_version,
    }


def _project_build_list_payload(
    project: SimulationProject,
    provenances: tuple[ModelicaBuildProvenance, ...],
) -> dict[str, object]:
    assets = {asset.asset_id: asset for asset in project.model_assets}
    builds = tuple(_project_build_payload(provenance, assets.get(provenance.fmu_asset_id)) for provenance in provenances)
    return {
        "success": True,
        "project_id": project.project_id,
        "schema_version": project.schema_version,
        "count": len(builds),
        "builds": builds,
    }


def _project_build_payload(
    provenance: ModelicaBuildProvenance,
    asset: ModelAsset | None,
) -> dict[str, object]:
    return {
        "build_id": provenance.build_id,
        "asset_id": provenance.fmu_asset_id,
        "model_name": provenance.model_name,
        "compiler_name": provenance.compiler_name,
        "compiler_version": provenance.compiler_version,
        "source_relative_path": provenance.source_snapshot.relative_path,
        "source_sha256": provenance.source_snapshot.sha256,
        "fmu_relative_path": asset.relative_path if asset is not None else None,
        "fmu_sha256": asset.sha256 if asset is not None else None,
    }


def _modelica_environment_payload(info: ModelicaEnvironmentInfo) -> dict[str, object]:
    return {
        "success": info.is_available,
        "status": info.status.name,
        "compiler_name": info.compiler_name,
        "compiler_version": info.compiler_version,
        "diagnostic_message": info.diagnostic_message,
    }


def _print_modelica_error(error: EngineError, *, as_json: bool) -> int:
    if as_json:
        print(
            json.dumps(
                {
                    "success": False,
                    "error": {
                        "code": error.code.value,
                        "message": error.message,
                        "details": error.details,
                    },
                },
                ensure_ascii=False,
                default=_json_default,
            ),
            file=sys.stderr,
        )
    else:
        _print_engine_error(error)
    return 1


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
        output_interval=args.output_interval,
        parameters=_parse_parameters(args.parameter),
        initial_inputs=_parse_parameters(args.input),
        selected_outputs=selected_outputs,
        execution_interface=(
            InterfaceType(args.interface) if args.interface is not None else None
        ),
    )


def _add_execution_interface_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--interface",
        choices=(InterfaceType.CO_SIMULATION.value, InterfaceType.MODEL_EXCHANGE.value),
        default=None,
        help="执行接口；省略时优先 Co-Simulation，其次可执行的 FMI2 Model Exchange",
    )


def _add_project_root_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project-root",
        required=True,
        metavar="PATH",
        help="包含 project.json 的 Project 目录",
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
    executable_interfaces = ", ".join(
        item.interface_type.value
        for item in metadata.interface_capabilities
        if item.can_execute
    )
    print(f"当前可用接口: {executable_interfaces or '无'}")
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
