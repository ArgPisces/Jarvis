use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyAny};
use serde_json::Value;
use std::collections::HashMap;
use std::fs;
use std::io::Read;
use std::path::Path;
use std::collections::HashSet;
use chrono::NaiveDateTime;
use regex::Regex;

fn parse_iso_naive(s: &str) -> Option<NaiveDateTime> {
    // Python datetime.isoformat() without timezone, e.g., "2025-09-22T12:34:56.123456"
    // Format: %Y-%m-%dT%H:%M:%S%.f (fractional seconds optional)
    NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S%.f").ok()
}

fn read_file_to_string(path: &Path) -> Option<String> {
    let mut f = fs::File::open(path).ok()?;
    let mut s = String::new();
    if f.read_to_string(&mut s).is_ok() {
        Some(s)
    } else {
        None
    }
}

fn load_json(path: &Path) -> Option<Value> {
    let text = read_file_to_string(path)?;
    serde_json::from_str::<Value>(&text).ok()
}

fn is_stats_file(path: &Path) -> bool {
    if !path.is_file() {
        return false;
    }
    match (path.file_stem(), path.extension()) {
        (Some(stem), Some(ext)) => {
            let stem_str = stem.to_string_lossy();
            let ext_str = ext.to_string_lossy();
            stem_str.starts_with("stats_") && ext_str == "json"
        }
        _ => false,
    }
}

fn value_to_f64(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n
            .as_f64()
            .or_else(|| n.as_i64().map(|x| x as f64))
            .or_else(|| n.as_u64().map(|x| x as f64)),
        _ => None,
    }
}

fn record_passes_time_and_tags(
    rec: &Value,
    start: &NaiveDateTime,
    end: &NaiveDateTime,
    tag_filter: &Option<HashMap<String, String>>,
) -> bool {
    // timestamp
    let ts = rec.get("timestamp").and_then(|v| v.as_str());
    if ts.is_none() {
        return false;
    }
    let ts_dt = match parse_iso_naive(ts.unwrap()) {
        Some(t) => t,
        None => return false,
    };
    if ts_dt < *start || ts_dt > *end {
        return false;
    }

    if let Some(filter) = tag_filter {
        let rec_tags = rec.get("tags").and_then(|t| t.as_object());
        if rec_tags.is_none() {
            return false;
        }
        let rec_tags = rec_tags.unwrap();
        for (k, v) in filter.iter() {
            match rec_tags.get(k) {
                Some(val) => {
                    if val.as_str().unwrap_or("") != v {
                        return false;
                    }
                }
                None => return false,
            }
        }
    }
    true
}

fn py_to_hashmap_str_str(tags: Option<Bound<'_, PyDict>>) -> Option<HashMap<String, String>> {
    // Let PyO3 extract directly into HashMap[str, str] if possible
    tags.and_then(|d| d.extract::<HashMap<String, String>>().ok())
}

#[pyfunction]
pub fn stats_get_metric_total(data_dir: &str, metric_name: &str) -> PyResult<f64> {
    let base = Path::new(data_dir);
    let mut total: f64 = 0.0;

    let entries = match fs::read_dir(base) {
        Ok(e) => e,
        Err(_) => return Ok(0.0),
    };

    for entry in entries {
        if let Ok(ent) = entry {
            let p = ent.path();
            if !is_stats_file(&p) {
                continue;
            }
            if let Some(v) = load_json(&p) {
                if let Some(metric_obj) = v.get(metric_name) {
                    if let Some(hours) = metric_obj.as_object() {
                        for (_, arr) in hours.iter() {
                            if let Some(records) = arr.as_array() {
                                for rec in records {
                                    if let Some(val) = value_to_f64(rec.get("value").unwrap_or(&Value::Null)) {
                                        total += val;
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    Ok(total)
}

#[pyfunction(signature = (data_dir, metric_name, start_iso, end_iso, tags=None))]
pub fn stats_get_metrics(
    py: Python<'_>,
    data_dir: &str,
    metric_name: &str,
    start_iso: &str,
    end_iso: &str,
    tags: Option<Bound<'_, PyDict>>,
) -> PyResult<Vec<PyObject>> {
    let start = parse_iso_naive(start_iso)
        .unwrap_or_else(|| parse_iso_naive("1970-01-01T00:00:00").unwrap());
    let end = parse_iso_naive(end_iso)
        .unwrap_or_else(|| parse_iso_naive("9999-12-31T23:59:59").unwrap());
    let tag_filter = py_to_hashmap_str_str(tags);

    let base = Path::new(data_dir);
    let mut out: Vec<(String, PyObject)> = Vec::new(); // keep timestamp for sorting

    let entries = match fs::read_dir(base) {
        Ok(e) => e,
        Err(_) => return Ok(vec![]),
    };

    for entry in entries {
        if let Ok(ent) = entry {
            let p = ent.path();
            if !is_stats_file(&p) {
                continue;
            }
            if let Some(v) = load_json(&p) {
                if let Some(metric_obj) = v.get(metric_name) {
                    if let Some(hours) = metric_obj.as_object() {
                        for (_hour_key, arr) in hours.iter() {
                            if let Some(records) = arr.as_array() {
                                for rec in records {
                                    if !record_passes_time_and_tags(rec, &start, &end, &tag_filter) {
                                        continue;
                                    }
                                    // Build Python dict: {"timestamp": str, "value": number, "tags": dict}
                                    if let Some(ts) = rec.get("timestamp").and_then(|t| t.as_str()) {
                                        let dict = PyDict::new_bound(py);
                                        dict.set_item("timestamp", ts).ok();
                                        if let Some(val) = value_to_f64(rec.get("value").unwrap_or(&Value::Null)) {
                                            dict.set_item("value", val).ok();
                                        } else if let Some(v) = rec.get("value") {
                                            // fallback: push as JSON string
                                            dict.set_item("value", v.to_string()).ok();
                                        }
                                        // tags
                                        if let Some(tobj) = rec.get("tags").and_then(|t| t.as_object()) {
                                            let tdict = PyDict::new_bound(py);
                                            for (k, v) in tobj.iter() {
                                                if let Some(sv) = v.as_str() {
                                                    tdict.set_item(k, sv).ok();
                                                } else {
                                                    tdict.set_item(k, v.to_string()).ok();
                                                }
                                            }
                                            dict.set_item("tags", tdict).ok();
                                        } else {
                                            dict.set_item("tags", PyDict::new_bound(py)).ok();
                                        }
                                        out.push((ts.to_string(), dict.into_any().unbind()));
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // sort by timestamp ascending (string ISO sorts lexicographically fine for same format)
    out.sort_by(|a, b| a.0.cmp(&b.0));

    Ok(out.into_iter().map(|(_, obj)| obj).collect())
}

#[pyfunction(signature = (data_dir, metric_name, start_iso, end_iso, aggregation, tags=None))]
pub fn stats_aggregate_metrics(
    py: Python<'_>,
    data_dir: &str,
    metric_name: &str,
    start_iso: &str,
    end_iso: &str,
    aggregation: &str,
    tags: Option<Bound<'_, PyDict>>,
) -> PyResult<PyObject> {
    let start = parse_iso_naive(start_iso)
        .unwrap_or_else(|| parse_iso_naive("1970-01-01T00:00:00").unwrap());
    let end = parse_iso_naive(end_iso)
        .unwrap_or_else(|| parse_iso_naive("9999-12-31T23:59:59").unwrap());
    let tag_filter = py_to_hashmap_str_str(tags);
    let base = Path::new(data_dir);

    #[derive(Clone, Copy)]
    struct Agg {
        count: u64,
        sum: f64,
        min_v: f64,
        max_v: f64,
    }

    let mut agg_map: HashMap<String, Agg> = HashMap::new();

    let entries = match fs::read_dir(base) {
        Ok(e) => e,
        Err(_) => {
            let res = PyDict::new_bound(py);
            return Ok(res.into_any().unbind());
        }
    };

    for entry in entries {
        if let Ok(ent) = entry {
            let p = ent.path();
            if !is_stats_file(&p) {
                continue;
            }
            if let Some(v) = load_json(&p) {
                if let Some(metric_obj) = v.get(metric_name) {
                    if let Some(hours) = metric_obj.as_object() {
                        for (_hour_key, arr) in hours.iter() {
                            if let Some(records) = arr.as_array() {
                                for rec in records {
                                    if !record_passes_time_and_tags(rec, &start, &end, &tag_filter) {
                                        continue;
                                    }
                                    let ts = match rec
                                        .get("timestamp")
                                        .and_then(|t| t.as_str())
                                        .and_then(|s| parse_iso_naive(s))
                                    {
                                        Some(t) => t,
                                        None => continue,
                                    };
                                    let val = match value_to_f64(rec.get("value").unwrap_or(&Value::Null)) {
                                        Some(v) => v,
                                        None => continue,
                                    };

                                    // determine key per aggregation
                                    let key = match aggregation {
                                        "hourly" => ts.format("%Y-%m-%d %H:00").to_string(),
                                        "daily" => ts.format("%Y-%m-%d").to_string(),
                                        _ => ts.format("%Y-%m-%d %H:00").to_string(),
                                    };

                                    let entry = agg_map.entry(key).or_insert(Agg {
                                        count: 0,
                                        sum: 0.0,
                                        min_v: f64::INFINITY,
                                        max_v: f64::NEG_INFINITY,
                                    });
                                    entry.count += 1;
                                    entry.sum += val;
                                    if val < entry.min_v {
                                        entry.min_v = val;
                                    }
                                    if val > entry.max_v {
                                        entry.max_v = val;
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // build Python dict result
    let res = PyDict::new_bound(py);
    for (k, a) in agg_map.iter() {
        let d = PyDict::new_bound(py);
        d.set_item("count", a.count).ok();
        d.set_item("sum", a.sum).ok();
        // if no records, min/max would be +/-inf; but since created only when we add records, it's safe
        d.set_item("min", a.min_v).ok();
        d.set_item("max", a.max_v).ok();
        let avg = if a.count > 0 { a.sum / (a.count as f64) } else { 0.0 };
        d.set_item("avg", avg).ok();
        res.set_item(k.as_str(), d).ok();
    }
    Ok(res.into_any().unbind())
}

#[pyfunction]
pub fn find_overlapping_groups(_py: Python<'_>, memories: Bound<'_, PyAny>, min_overlap: usize) -> PyResult<Vec<Vec<usize>>> {
    // Extract tags list from memories (Python list of dicts with key "tags")
    let list = memories.downcast::<PyList>()?;
    let mut tags_vec: Vec<std::collections::HashSet<String>> = Vec::with_capacity(list.len());
    for item in list.iter() {
        // Each item should be a dict
        let dict = match item.downcast::<PyDict>() {
            Ok(d) => d,
            Err(_) => {
                tags_vec.push(std::collections::HashSet::new());
                continue;
            }
        };
        let mut s: std::collections::HashSet<String> = std::collections::HashSet::new();
        if let Ok(Some(tags_obj)) = dict.get_item("tags") {
            if let Ok(tag_list) = tags_obj.downcast::<PyList>() {
                for t in tag_list.iter() {
                    if let Ok(st) = t.extract::<String>() {
                        s.insert(st);
                    }
                }
            }
        }
        tags_vec.push(s);
    }

    let n = tags_vec.len();
    let mut groups: Vec<Vec<usize>> = Vec::new();
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();

    // helper to compute intersection size
    let inter_size = |a: &std::collections::HashSet<String>, b: &std::collections::HashSet<String>| -> usize {
        if a.len() < b.len() {
            a.iter().filter(|x| b.contains(*x)).count()
        } else {
            b.iter().filter(|x| a.contains(*x)).count()
        }
    };

    for i in 0..n {
        for j in (i + 1)..n {
            let ov = inter_size(&tags_vec[i], &tags_vec[j]);
            if ov >= min_overlap {
                let mut group: std::collections::BTreeSet<usize> = [i, j].into_iter().collect();

                // Expand group: include any k that has min overlap with all members >= min_overlap
                let mut changed = true;
                while changed {
                    changed = false;
                    for k in 0..n {
                        if group.contains(&k) {
                            continue;
                        }
                        // compute min overlap with current group
                        let mut min_ov = usize::MAX;
                        for &m in group.iter() {
                            let ov2 = inter_size(&tags_vec[k], &tags_vec[m]);
                            if ov2 < min_ov {
                                min_ov = ov2;
                            }
                            if min_ov < min_overlap {
                                break;
                            }
                        }
                        if min_ov >= min_overlap {
                            group.insert(k);
                            changed = true;
                        }
                    }
                }

                let v: Vec<usize> = group.into_iter().collect();
                // Use sorted key to deduplicate
                let key = v.iter().map(|x| x.to_string()).collect::<Vec<_>>().join(",");
                if !seen.contains(&key) {
                    seen.insert(key);
                    groups.push(v);
                }
            }
        }
    }

    Ok(groups)
}

#[pyfunction]
pub fn stats_list_metrics(data_dir: &str, totals_dir: &str, meta_file: &str) -> PyResult<Vec<String>> {
    let mut set: HashSet<String> = HashSet::new();

    // 1) meta file: metrics keys
    let meta_path = Path::new(meta_file);
    if meta_path.exists() && meta_path.is_file() {
        if let Some(v) = load_json(meta_path) {
            if let Some(obj) = v.get("metrics").and_then(|m| m.as_object()) {
                for (k, _) in obj.iter() {
                    set.insert(k.to_string());
                }
            }
        }
    }

    // 2) data files: top-level metric names
    let data_path = Path::new(data_dir);
    if let Ok(entries) = fs::read_dir(data_path) {
        for entry in entries.flatten() {
            let p = entry.path();
            if !is_stats_file(&p) {
                continue;
            }
            if let Some(v) = load_json(&p) {
                if let Some(obj) = v.as_object() {
                    for (k, _) in obj.iter() {
                        set.insert(k.to_string());
                    }
                }
            }
        }
    }

    // 3) totals dir: file names
    let totals_path = Path::new(totals_dir);
    if let Ok(entries) = fs::read_dir(totals_path) {
        for entry in entries.flatten() {
            let p = entry.path();
            if p.is_file() {
                if let Some(name) = p.file_name().and_then(|s| s.to_str()) {
                    set.insert(name.to_string());
                }
            }
        }
    }

    // return sorted
    let mut v: Vec<String> = set.into_iter().collect();
    v.sort();
    Ok(v)
}

#[pyfunction]
pub fn git_modified_line_ranges(py: Python<'_>) -> PyResult<PyObject> {
    use std::process::Command;

    let output = match Command::new("git").arg("show").output() {
        Ok(o) => o,
        Err(_) => {
            let d = PyDict::new_bound(py);
            return Ok(d.into_any().unbind());
        }
    };
    let stdout = String::from_utf8_lossy(&output.stdout);

    let re_range = Regex::new(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@").unwrap();

    let result = PyDict::new_bound(py);
    let mut current_file: Option<String> = None;

    for line in stdout.lines() {
        if let Some(rest) = line.strip_prefix("+++ b/") {
            current_file = Some(rest.to_string());
            continue;
        }
        if let Some(file) = &current_file {
            if let Some(caps) = re_range.captures(line) {
                if let Some(m1) = caps.get(1) {
                    let start: i64 = m1.as_str().parse().unwrap_or(1);
                    let count: i64 = caps.get(2).map(|m| m.as_str().parse().unwrap_or(1)).unwrap_or(1);
                    let end = start + count - 1;

                    // append tuple (start, end) into list for this file
                    let list_any = result.get_item(file);
                    if let Ok(Some(obj)) = list_any {
                        if let Ok(list) = obj.downcast::<pyo3::types::PyList>() {
                            let tup: Py<PyAny> = (start, end).into_py(py);
                            list.append(tup).ok();
                        } else {
                            // overwrite with new list if unexpected type
                            let l = pyo3::types::PyList::empty_bound(py);
                            let tup: Py<PyAny> = (start, end).into_py(py);
                            l.append(tup).ok();
                            result.set_item(file, l).ok();
                        }
                    } else {
                        let l = pyo3::types::PyList::empty_bound(py);
                        let tup: Py<PyAny> = (start, end).into_py(py);
                        l.append(tup).ok();
                        result.set_item(file, l).ok();
                    }
                }
            }
        }
    }

    Ok(result.into_any().unbind())
}
