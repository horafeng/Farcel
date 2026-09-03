"""Run FMI2 Model Exchange through Farcel's public API only."""

from __future__ import annotations

import sys
from pathlib import Path

from farcel import create_backend
from farcel.contracts import EngineError, InterfaceType, SimulationConfig


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FMU = REPOSITORY_ROOT / "examples" / "fmus" / "VanDerPol.fmu"


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    fmu_path = Path(arguments[0]).resolve() if arguments else DEFAULT_FMU
    csv_path = REPOSITORY_ROOT / "artifacts" / f"{fmu_path.stem}-model-exchange.csv"
    backend = create_backend()
    try:
        metadata = backend.load_fmu(fmu_path)
        config = SimulationConfig(
            stop_time=0.05,
            communication_step=0.01,
            parameters={"mu": 2.0},
            selected_outputs=("x0",),
            execution_interface=InterfaceType.MODEL_EXCHANGE,
        )
        backend.validate_config(metadata, config)
        result = backend.run_fmu(fmu_path, config)
        report = backend.export_result(result, csv_path)
        print(f"model: {metadata.model_name}")
        print(f"interface: {config.execution_interface.value}")
        print(f"completed steps: {result.completed_steps}")
        print(f"final time: {result.final_time}")
        print(f"final x0: {result.outputs['x0'][-1]}")
        print(f"CSV rows: {report.row_count}")
        return 0
    except EngineError as error:
        print(f"{error.code.value}: {error.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
