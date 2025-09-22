use pyo3::prelude::*;
use regex::Regex;

#[pyfunction]
pub fn extract_tool_call_blocks(
    content: &str,
    open_tag: &str,
    close_tag: &str,
) -> PyResult<(Vec<String>, bool)> {
    let open_esc = regex::escape(open_tag);
    let close_esc = regex::escape(close_tag);
    let pattern = format!(r"(?ms)^{open}(.*?)^{close}", open = open_esc, close = close_esc);
    let re = Regex::new(&pattern)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    let mut blocks: Vec<String> = Vec::new();
    for cap in re.captures_iter(content) {
        if let Some(m) = cap.get(1) {
            blocks.push(m.as_str().to_string());
        }
    }
    let mut auto_completed = false;

    if blocks.is_empty() {
        let open_re = Regex::new(&format!(r"(?m)^{open}", open = open_esc))
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        let close_re = Regex::new(&format!(r"(?m)^{close}", close = close_esc))
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        let has_open_at_bol = open_re.is_match(content);
        let has_close_at_bol = close_re.is_match(content);

        if has_open_at_bol && !has_close_at_bol {
            let mut fixed = content.trim_end_matches(&['\n', '\r'][..]).to_string();
            fixed.push('\n');
            fixed.push_str(close_tag);
            for cap in re.captures_iter(&fixed) {
                if let Some(m) = cap.get(1) {
                    blocks.push(m.as_str().to_string());
                }
            }
            if !blocks.is_empty() {
                auto_completed = true;
            }
        }
    }

    Ok((blocks, auto_completed))
}
