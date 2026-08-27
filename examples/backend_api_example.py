"""End-to-end use of Farcel's public backend API, without CLI or adapter imports."""

from __future__ import annotations

import sys
from pathlib import Path

from farcel import create_backend
from farcel.contracts import EngineError, SimulationConfig


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FMU = REPOSITORY_ROOT / "examples" / "fmus" / "VanDerPol.fmu"


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    fmu_path = Path(arguments[0]).resolve() if arguments else DEFAULT_FMU
    csv_path = REPOSITORY_ROOT / "artifacts" / f"{fmu_path.stem}-backend-api.csv"
    backend = create_backend()

    try:
        metadata = backend.load_fmu(fmu_path)
        print(f"model: {metadata.model_name}")
        print(f"FMI version: {metadata.fmi_version}")
        print(f"executable interface: {metadata.executable_interface.value}")

        config = SimulationConfig(
            start_time=0.0,
            stop_time=0.02,
            communication_step=0.01,
            parameters={"mu": 2.0},
            selected_outputs=("x0",),
        )
        report = backend.validate_config(metadata, config)
        print(f"validation successful: {report.is_valid}")

        result = backend.run_fmu(fmu_path, config)
        print(f"completed steps: {result.completed_steps}")
        print(f"samples: {result.sample_count}")
        print(f"final time: {result.final_time}")
        print(f"selected outputs: {', '.join(result.outputs)}")
        print(f"first sample: time={result.timestamps[0]}, x0={result.outputs['x0'][0]}")
        print(f"last sample: time={result.timestamps[-1]}, x0={result.outputs['x0'][-1]}")

        export = backend.export_result(result, csv_path)
        print(f"CSV: {export.destination}")
        print(f"CSV rows: {export.row_count}")
        return 0
    except EngineError as error:
        print(f"{error.code.value}: {error.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
