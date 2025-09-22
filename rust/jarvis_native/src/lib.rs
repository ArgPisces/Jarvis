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

    // file utils
    m.add_function(wrap_pyfunction!(file_utils::get_file_md5, m)?)?;
    m.add_function(wrap_pyfunction!(file_utils::get_file_line_count, m)?)?;

    // toolcall
    m.add_function(wrap_pyfunction!(toolcall::extract_tool_call_blocks, m)?)?;

    Ok(())
}
