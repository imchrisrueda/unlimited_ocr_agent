"""Módulo de benchmark offline, determinista y auditable para extracción de estadillos."""

from .schemas import (
    MetricValue,
    CaseMetrics,
    AggregatedMetrics,
    PipelineConfig,
    BenchmarkCase,
    BenchmarkManifest,
    GroundTruthDocument,
    BenchmarkPrediction,
    BenchmarkReport,
    validate_confined_relative_path,
)
from .matching import (
    RowMatch,
    MatchResult,
    match_rows_deterministic,
)
from .metrics import (
    compute_case_metrics,
)
from .report import (
    aggregate_metrics,
    generate_benchmark_report,
    render_report_markdown,
    save_benchmark_report,
)

__all__ = [
    "MetricValue",
    "CaseMetrics",
    "AggregatedMetrics",
    "PipelineConfig",
    "BenchmarkCase",
    "BenchmarkManifest",
    "GroundTruthDocument",
    "BenchmarkPrediction",
    "BenchmarkReport",
    "validate_confined_relative_path",
    "RowMatch",
    "MatchResult",
    "match_rows_deterministic",
    "compute_case_metrics",
    "aggregate_metrics",
    "generate_benchmark_report",
    "render_report_markdown",
    "save_benchmark_report",
]
