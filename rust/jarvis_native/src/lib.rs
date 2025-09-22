//! Jarvis native extension - module root
//! Modules: stats (file IO + JSON metrics), file_utils (helpers), toolcall (regex extract)

use pyo3::prelude::*;
use pyo3::types::PyModule;

mod stats;
mod file_utils;
mod toolcall;

#[pymodule]
fn jarvis_native(_py: Python, m: &Bound<PyModule>) -> PyResult<()> {
    // stats
    m.add_function(wrap_pyfunction!(stats::stats_get_metrics, m)?)?;
    m.add_function(wrap_pyfunction!(stats::stats_get_metric_total, m)?)?;
    m.add_function(wrap_pyfunction!(stats::stats_aggregate_metrics, m)?)?;
    m.add_function(wrap_pyfunction!(stats::find_overlapping_groups, m)?)?;
    m.add_function(wrap_pyfunction!(stats::stats_list_metrics, m)?)?;
    m.add_function(wrap_pyfunction!(stats::git_modified_line_ranges, m)?)?;

    // file utils
    m.add_function(wrap_pyfunction!(file_utils::get_file_md5, m)?)?;
    m.add_function(wrap_pyfunction!(file_utils::get_file_line_count, m)?)?;
    m.add_function(wrap_pyfunction!(file_utils::list_git_files, m)?)?;
    m.add_function(wrap_pyfunction!(file_utils::list_files_excluding_git, m)?)?;
    m.add_function(wrap_pyfunction!(file_utils::git_recent_commits_with_files, m)?)?;
    m.add_function(wrap_pyfunction!(file_utils::git_get_commits_between, m)?)?;
    m.add_function(wrap_pyfunction!(file_utils::git_get_latest_commit_hash, m)?)?;
    m.add_function(wrap_pyfunction!(file_utils::git_has_uncommitted_changes, m)?)?;
    m.add_function(wrap_pyfunction!(file_utils::git_is_file_in_repo, m)?)?;
    m.add_function(wrap_pyfunction!(file_utils::git_get_diff_file_list, m)?)?;
    m.add_function(wrap_pyfunction!(file_utils::git_list_untracked_files, m)?)?;
    m.add_function(wrap_pyfunction!(file_utils::git_find_root, m)?)?;

    // toolcall
    m.add_function(wrap_pyfunction!(toolcall::extract_tool_call_blocks, m)?)?;

    Ok(())
}
