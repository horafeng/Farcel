#!/usr/bin/env python3
"""编译 Phase 9.0 Modelica 探针，并通过 Farcel 验证生成的 FMU。

这是开发验证工具，不是生产编译器适配器。它使用 OpenModelica 官方 scripting API，
在产出 FMU 后只调用 Farcel 公共后端 API。
"""

from __future__ import annotations

import argparse
import math
import os
import platform
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
import zipfile
from dataclasses import dataclass
from pathlib import Path

from farcel import create_backend
from farcel.contracts import InterfaceType, SimulationConfig, SimulationState


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = REPOSITORY_ROOT / "examples" / "modelica" / "FarcelFirstOrder.mo"
DEFAULT_WORK_DIRECTORY = REPOSITORY_ROOT / "artifacts" / "modelica"


@dataclass(frozen=True, slots=True)
class CompilationResult:
    fmu_path: Path
    omc_output: str


def discover_omc(explicit_path: str | None = None) -> Path | None:
    """Find OMC without embedding a developer-specific installation path."""

    if explicit_path is not None:
        candidate = Path(explicit_path)
        return candidate.resolve() if candidate.is_file() else None

    candidates: list[Path] = []
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


def _check_model_succeeded(output: str) -> bool:
    """Accept the exact first-line success result produced by OMC 1.26.3."""

    return (
        _find_marker(output, "FARCEL_CHECK_MODEL")
        == "Check of FarcelFirstOrder completed successfully."
    )


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
    if not _check_model_succeeded(output):
        check_result = _find_marker(output, "FARCEL_CHECK_MODEL")
        raise RuntimeError(
            "OMC checkModel() 未返回预期成功结果: "
            + (check_result if check_result is not None else "未报告结果")
        )
    fmu_name = _find_marker(output, "FARCEL_FMU_PATH")
    if not fmu_name:
        raise RuntimeError("OMC buildModelFMU() 未报告 FMU 产物路径")
    fmu_path = Path(fmu_name)
    if not fmu_path.is_absolute():
        fmu_path = work_directory / fmu_path
    if not fmu_path.is_file():
        raise RuntimeError(f"OMC 报告的 FMU 产物不存在: {fmu_path}")
    return CompilationResult(fmu_path.resolve(), output)


def validate_fmu_structure(fmu_path: Path) -> str:
    with zipfile.ZipFile(fmu_path) as archive:
        names = set(archive.namelist())
        if "modelDescription.xml" not in names:
            raise RuntimeError("FMU 压缩包缺少 modelDescription.xml")
        root = ElementTree.fromstring(archive.read("modelDescription.xml"))
        co_simulation = root.find("{*}CoSimulation")
        model_identifier = (
            co_simulation.attrib.get("modelIdentifier")
            if co_simulation is not None
            else None
        )
        if (
            os.name == "nt"
            and platform.machine().lower() in {"amd64", "x86_64"}
            and (
                not model_identifier
                or f"binaries/win64/{model_identifier}.dll" not in names
            )
        ):
            raise RuntimeError("FMU 缺少当前 Windows x64 Co-Simulation 原生 DLL")
    if root.attrib.get("fmiVersion") != "2.0":
        raise RuntimeError("生成的 FMU 不是 FMI 2.0")
    if co_simulation is None or not model_identifier:
        raise RuntimeError("生成的 FMU 不包含 CoSimulation 接口")
    return model_identifier


def _sample_at(result, target: float) -> float:
    for timestamp, value in zip(result.timestamps, result.outputs["y"]):
        if math.isclose(timestamp, target, rel_tol=0.0, abs_tol=1e-9):
            return float(value)
    raise RuntimeError(f"仿真结果缺少 t={target:g} 的采样点")


def _validate_simulation_result(result, time_constant: float) -> tuple[dict[float, float], float]:
    if result.completion_state is not SimulationState.COMPLETED:
        raise RuntimeError(f"Farcel 仿真未正常完成: {result.completion_state.value}")
    if not result.timestamps:
        raise RuntimeError("Farcel 结果时间轴为空")
    if any(not math.isfinite(timestamp) for timestamp in result.timestamps):
        raise RuntimeError("Farcel 结果时间轴包含非有限数值")
    if any(left >= right for left, right in zip(result.timestamps, result.timestamps[1:])):
        raise RuntimeError("Farcel 结果时间轴不是严格递增")
    if not math.isclose(result.timestamps[0], 0.0, rel_tol=0.0, abs_tol=1e-9):
        raise RuntimeError("Farcel 返回了错误的起始时间")
    if not math.isclose(result.final_time, 2.0, rel_tol=0.0, abs_tol=1e-9):
        raise RuntimeError("Farcel 返回了错误的终止时间")
    values = result.outputs.get("y")
    if values is None or len(values) != len(result.timestamps):
        raise RuntimeError("Farcel y 输出序列与时间轴长度不一致")
    if any(not math.isfinite(float(value)) for value in values):
        raise RuntimeError("Farcel 结果包含非有限 y 值")

    samples = {target: _sample_at(result, target) for target in (0.0, 0.5, 1.0, 2.0)}
    errors = {
        target: abs(value - (1.0 - math.exp(-target / time_constant)))
        for target, value in samples.items()
    }
    largest_error = max(errors.values())
    if largest_error > 0.03:
        raise RuntimeError(f"数值误差超过 0.03：最大误差 {largest_error:.6g}")
    return samples, largest_error


def _probe_config(time_constant: float) -> SimulationConfig:
    return SimulationConfig(
        start_time=0.0,
        stop_time=2.0,
        communication_step=0.01,
        parameters={"T": time_constant},
        initial_inputs={"u": 1.0},
        selected_outputs=("y",),
    )


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

    baseline_config = _probe_config(0.5)
    backend.validate_config(metadata, baseline_config)
    baseline_result = backend.run_fmu(fmu_path, baseline_config)
    baseline_values, baseline_error = _validate_simulation_result(baseline_result, 0.5)
    baseline_export = backend.export_result(
        baseline_result, work_directory / "FarcelFirstOrder.csv"
    )
    if baseline_export.row_count != baseline_result.sample_count:
        raise RuntimeError("T=0.5 CSV 导出行数与仿真结果样本数不一致")

    slower_config = _probe_config(1.0)
    backend.validate_config(metadata, slower_config)
    slower_result = backend.run_fmu(fmu_path, slower_config)
    slower_values, slower_error = _validate_simulation_result(slower_result, 1.0)
    if any(
        slower_values[target] >= baseline_values[target]
        for target in (0.5, 1.0, 2.0)
    ):
        raise RuntimeError("T=1.0 未产生比 T=0.5 更慢的一阶响应")
    slower_export = backend.export_result(
        slower_result, work_directory / "FarcelFirstOrder-T1.csv"
    )
    if slower_export.row_count != slower_result.sample_count:
        raise RuntimeError("T=1.0 CSV 导出行数与仿真结果样本数不一致")

    print(f"Farcel T=0.5 数值验证: y={baseline_values}; 最大误差={baseline_error:.6g}")
    print(f"Farcel T=0.5 CSV: {baseline_export.destination}，行数={baseline_export.row_count}")
    print(f"Farcel T=1.0 数值验证: y={slower_values}; 最大误差={slower_error:.6g}")
    print(f"Farcel T=1.0 CSV: {slower_export.destination}，行数={slower_export.row_count}")


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
