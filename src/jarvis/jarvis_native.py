# -*- coding: utf-8 -*-
"""
Native bindings for Jarvis via PyO3 (required).

This module acts as a thin shim inside the jarvis package. It attempts to import
the compiled extension module named 'jarvis_native' (built via PyO3/maturin).
If the native extension is available, its symbols are re-exported; otherwise,
this module remains importable but empty, allowing graceful Python fallback.

Exposed functions (when native extension is present):
  - stats_get_metrics(data_dir: str, metric_name: str, start_iso: str, end_iso: str, tags: Optional[dict]) -> list[dict]
  - stats_get_metric_total(data_dir: str, metric_name: str) -> float
  - stats_aggregate_metrics(data_dir: str, metric_name: str, start_iso: str, end_iso: str, aggregation: str, tags: Optional[dict]) -> dict
"""
from jarvis_native import (  # type: ignore
    stats_get_metrics,
    stats_get_metric_total,
    stats_aggregate_metrics,
    get_file_md5,
    get_file_line_count,
    extract_tool_call_blocks,
)
__all__ = [
    "stats_get_metrics",
    "stats_get_metric_total",
    "stats_aggregate_metrics",
    "get_file_md5",
    "get_file_line_count",
    "extract_tool_call_blocks",
]
