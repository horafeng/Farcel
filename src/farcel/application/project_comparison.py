"""Pure historical project-run comparison and scalar post-processing."""

from __future__ import annotations

import math
from numbers import Real

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.project_comparison import (
    ProjectRunComparison,
    ProjectRunSignalComparison,
    ProjectRunSignalSeries,
    SignalStatistics,
    SignalStatisticsStatus,
)
from farcel.contracts.project_result import ProjectRunArtifact


class ProjectRunComparisonService:
    """Compares only caller-supplied immutable historical artifacts."""

    def compare(
        self,
        artifacts: tuple[ProjectRunArtifact, ...],
    ) -> ProjectRunComparison:
        self._validate_artifacts(artifacts)
        signal_keys = sorted(
            {
                (node_id, variable_name)
                for artifact in artifacts
                for node_id, outputs in artifact.result.node_outputs.items()
                for variable_name in outputs
            }
        )
        signals = tuple(
            ProjectRunSignalComparison(
                node_id=node_id,
                variable_name=variable_name,
                series=tuple(
                    self._series_for(artifact, node_id, variable_name)
                    for artifact in artifacts
                ),
            )
            for node_id, variable_name in signal_keys
        )
        return ProjectRunComparison(sources=artifacts, signals=signals)

    @staticmethod
    def _validate_artifacts(artifacts: tuple[ProjectRunArtifact, ...]) -> None:
        if not artifacts:
            raise _comparison_configuration_error(
                "artifacts",
                "EMPTY_COMPARISON_ARTIFACTS",
                "至少需要一个历史运行 artifact 才能比较",
            )

        seen_run_ids: set[str] = set()
        for index, artifact in enumerate(artifacts):
            if artifact.run_id in seen_run_ids:
                raise _comparison_configuration_error(
                    f"artifacts[{index}].run_id",
                    "DUPLICATE_COMPARISON_RUN_ID",
                    "比较输入中的 run_id 必须唯一",
                )
            seen_run_ids.add(artifact.run_id)

    @staticmethod
    def _series_for(
        artifact: ProjectRunArtifact,
        node_id: str,
        variable_name: str,
    ) -> ProjectRunSignalSeries:
        outputs = artifact.result.node_outputs.get(node_id)
        if outputs is None or variable_name not in outputs:
            return ProjectRunSignalSeries(
                run_id=artifact.run_id,
                timestamps=(),
                samples=(),
                statistics=SignalStatistics(SignalStatisticsStatus.MISSING_SIGNAL),
            )

        samples = outputs[variable_name]
        return ProjectRunSignalSeries(
            run_id=artifact.run_id,
            timestamps=artifact.result.timestamps,
            samples=samples,
            statistics=_statistics_for(samples),
        )


def _statistics_for(samples: tuple[object, ...]) -> SignalStatistics:
    if not samples:
        return SignalStatistics(SignalStatisticsStatus.EMPTY_SIGNAL)
    if any(isinstance(sample, (tuple, list)) for sample in samples):
        return SignalStatistics(SignalStatisticsStatus.ARRAY_SIGNAL)
    if not all(isinstance(sample, Real) and not isinstance(sample, bool) for sample in samples):
        return SignalStatistics(SignalStatisticsStatus.NON_NUMERIC_SIGNAL)

    numeric_samples = tuple(float(sample) for sample in samples)
    return SignalStatistics(
        status=SignalStatisticsStatus.AVAILABLE,
        minimum=min(numeric_samples),
        maximum=max(numeric_samples),
        mean=math.fsum(numeric_samples) / len(numeric_samples),
        final=numeric_samples[-1],
    )


def _comparison_configuration_error(
    field: str,
    code: str,
    message: str,
) -> EngineError:
    return EngineError(
        ErrorCode.CONFIG_ERROR,
        "历史运行比较配置无效",
        {"issues": ({"field": field, "code": code, "message": message},)},
    )
