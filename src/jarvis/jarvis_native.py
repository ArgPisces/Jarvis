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
    list_git_files,
    list_files_excluding_git,
    git_recent_commits_with_files,
    git_get_commits_between,
    git_get_latest_commit_hash,
    git_has_uncommitted_changes,
    git_is_file_in_repo,
    git_get_diff_file_list,
    git_list_untracked_files,
    git_find_root,
    extract_tool_call_blocks,
    find_overlapping_groups,
    stats_list_metrics,
    git_modified_line_ranges,
)
__all__ = [
    "stats_get_metrics",
    "stats_get_metric_total",
    "stats_aggregate_metrics",
    "get_file_md5",
    "get_file_line_count",
    "list_git_files",
    "list_files_excluding_git",
    "git_recent_commits_with_files",
    "git_get_commits_between",
    "git_get_latest_commit_hash",
    "git_has_uncommitted_changes",
    "git_is_file_in_repo",
    "git_get_diff_file_list",
    "git_list_untracked_files",
    "git_find_root",
    "extract_tool_call_blocks",
    "find_overlapping_groups",
    "stats_list_metrics",
    "git_modified_line_ranges",
]
