#!/usr/bin/env python3
"""编译 Phase 9.0 Modelica 探针，并通过 Farcel 验证生成的 FMU。

这是开发验证工具，不是生产编译器适配器。它使用 OpenModelica 官方 scripting API，
在产出 FMU 后只调用 Farcel 公共后端 API。
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
import zipfile
from dataclasses import dataclass
from pathlib import Path

from farcel import create_backend
from farcel.contracts import InterfaceType, SimulationConfig


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = REPOSITORY_ROOT / "examples" / "modelica" / "FarcelFirstOrder.mo"
DEFAULT_WORK_DIRECTORY = REPOSITORY_ROOT / "artifacts" / "modelica"


@dataclass(frozen=True, slots=True)
class CompilationResult:
    fmu_path: Path
    omc_output: str


def discover_omc(explicit_path: str | None = None) -> Path | None:
    """Find OMC without embedding a developer-specific installation path."""

    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path))

    openmodelica_home = os.environ.get("OPENMODELICAHOME")
    if openmodelica_home:
        candidates.append(Path(openmodelica_home) / "bin" / _omc_name())

    from_path = shutil.which("omc")
    if from_path:
        candidates.append(Path(from_path))

    if os.name == "nt":
        for base in (Path("C:/OpenModelica"), Path("C:/Program Files")):
            candidates.extend(base.glob("OpenModelica*/bin/omc.exe"))

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _omc_name() -> str:
    return "omc.exe" if os.name == "nt" else "omc"


def _modelica_string(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace('"', '\\"')


def _write_omc_script(model_path: Path, work_directory: Path) -> Path:
    script_path = work_directory / "build_farcel_first_order.mos"
    script_path.write_text(
        "\n".join(
            (
                f'cd("{_modelica_string(work_directory)}");',
                f'loadOk := loadFile("{_modelica_string(model_path)}");',
                'print("FARCEL_LOAD_FILE=" + String(loadOk) + "\\n");',
                'checkResult := checkModel(FarcelFirstOrder);',
                'print("FARCEL_CHECK_MODEL=" + checkResult + "\\n");',
                'fmuResult := buildModelFMU(FarcelFirstOrder, version="2.0", fmuType="cs");',
                'print("FARCEL_FMU_PATH=" + fmuResult + "\\n");',
                'print("FARCEL_DIAGNOSTICS_BEGIN\\n" + getErrorString() + "FARCEL_DIAGNOSTICS_END\\n");',
                "exit();",
                "",
            )
        ),
        encoding="utf-8",
    )
    return script_path


def _find_marker(output: str, name: str) -> str | None:
    prefix = f"{name}="
    for line in output.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def compile_fmu(
    omc_path: Path,
    model_path: Path,
    work_directory: Path,
    timeout_seconds: float,
) -> CompilationResult:
    work_directory.mkdir(parents=True, exist_ok=True)
    script_path = _write_omc_script(model_path, work_directory)
    command = [str(omc_path), str(script_path)]
    print(f"运行 OMC: {command!r}")
    try:
        completed = subprocess.run(
            command,
            cwd=work_directory,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"OMC 在 {timeout_seconds:g} 秒内未完成，进程已终止") from None
    output = f"{completed.stdout}{completed.stderr}"
    print(output, end="" if output.endswith("\n") else "\n")
    if completed.returncode != 0:
        raise RuntimeError(f"OMC 编译失败，退出码 {completed.returncode}")
    if _find_marker(output, "FARCEL_LOAD_FILE") != "true":
        raise RuntimeError("OMC loadFile() 未返回 true")
    fmu_name = _find_marker(output, "FARCEL_FMU_PATH")
    if not fmu_name:
        raise RuntimeError("OMC buildModelFMU() 未报告 FMU 产物路径")
    fmu_path = Path(fmu_name)
    if not fmu_path.is_absolute():
        fmu_path = work_directory / fmu_path
    if not fmu_path.is_file():
        raise RuntimeError(f"OMC 报告的 FMU 产物不存在: {fmu_path}")
    return CompilationResult(fmu_path.resolve(), output)


def validate_fmu_structure(fmu_path: Path) -> None:
    with zipfile.ZipFile(fmu_path) as archive:
        names = set(archive.namelist())
        if "modelDescription.xml" not in names:
            raise RuntimeError("FMU 压缩包缺少 modelDescription.xml")
        root = ElementTree.fromstring(archive.read("modelDescription.xml"))
    if root.attrib.get("fmiVersion") != "2.0":
        raise RuntimeError("生成的 FMU 不是 FMI 2.0")
    if root.find("{*}CoSimulation") is None:
        raise RuntimeError("生成的 FMU 不包含 CoSimulation 接口")


def _sample_at(result, target: float) -> float:
    for timestamp, value in zip(result.timestamps, result.outputs["y"]):
        if math.isclose(timestamp, target, rel_tol=0.0, abs_tol=1e-9):
            return float(value)
    raise RuntimeError(f"仿真结果缺少 t={target:g} 的采样点")


def run_farcel_probe(fmu_path: Path, work_directory: Path) -> None:
    backend = create_backend()
    metadata = backend.load_fmu(fmu_path)
    if metadata.fmi_version != "2.0":
        raise RuntimeError(f"Farcel 读取到意外的 FMI 版本: {metadata.fmi_version}")
    if metadata.executable_interface is not InterfaceType.CO_SIMULATION:
        raise RuntimeError(
            "Farcel 未将生成的 FMU 判定为当前平台可执行的 Co-Simulation: "
            + "; ".join(metadata.diagnostics)
        )

    config = SimulationConfig(
        start_time=0.0,
        stop_time=2.0,
        communication_step=0.01,
        initial_inputs={"u": 1.0},
        selected_outputs=("y",),
    )
    backend.validate_config(metadata, config)
    result = backend.run_fmu(fmu_path, config)
    if result.final_time != 2.0 or result.timestamps[0] != 0.0:
        raise RuntimeError("Farcel 返回了错误的仿真时间范围")
    if any(not math.isfinite(float(value)) for value in result.outputs["y"]):
        raise RuntimeError("Farcel 结果包含非有限 y 值")
    values = {target: _sample_at(result, target) for target in (0.0, 0.5, 1.0, 2.0)}
    errors = {
        target: abs(value - (1.0 - math.exp(-target / 0.5)))
        for target, value in values.items()
    }
    largest_error = max(errors.values())
    if largest_error > 0.03:
        raise RuntimeError(f"数值误差超过 0.03：最大误差 {largest_error:.6g}")
    report = backend.export_result(result, work_directory / "FarcelFirstOrder.csv")
    if report.row_count != result.sample_count:
        raise RuntimeError("CSV 导出行数与仿真结果样本数不一致")
    print(f"Farcel 数值验证: y={values}; 最大误差={largest_error:.6g}")
    print(f"Farcel CSV: {report.destination}，行数={report.row_count}")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--omc", help="OMC 可执行文件路径；未提供时自动发现")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIRECTORY)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--skip-farcel-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    if arguments.timeout_seconds <= 0:
        print("--timeout-seconds 必须为正数", file=sys.stderr)
        return 3
    model_path = arguments.model.resolve()
    if not model_path.is_file():
        print(f"Modelica 模型不存在: {model_path}", file=sys.stderr)
        return 3
    omc_path = discover_omc(arguments.omc)
    if omc_path is None:
        print("未找到 OpenModelica OMC；请使用 --omc 或配置 OPENMODELICAHOME/PATH。", file=sys.stderr)
        return 2
    try:
        version = subprocess.run(
            [str(omc_path), "--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15, check=False,
        )
        print(f"OMC 路径: {omc_path}")
        print(f"OMC 版本: {(version.stdout or version.stderr).strip()}")
        if version.returncode != 0:
            raise RuntimeError(f"OMC --version 失败，退出码 {version.returncode}")
        compilation = compile_fmu(
            omc_path, model_path, arguments.work_dir.resolve(), arguments.timeout_seconds
        )
        validate_fmu_structure(compilation.fmu_path)
        print(f"FMU 结构验证成功: {compilation.fmu_path}")
        if not arguments.skip_farcel_run:
            run_farcel_probe(compilation.fmu_path, arguments.work_dir.resolve())
    except (OSError, RuntimeError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        print(f"OpenModelica 预验证失败: {error}", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
