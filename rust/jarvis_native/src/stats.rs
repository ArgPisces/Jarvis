use pyo3::prelude::*;
use pyo3::types::PyDict;
use serde_json::Value;
use std::collections::HashMap;
use std::fs;
use std::io::Read;
use std::path::Path;
use chrono::NaiveDateTime;

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
        .unwrap_or_else(|| NaiveDateTime::from_timestamp_opt(0, 0).unwrap());
    let end = parse_iso_naive(end_iso)
        .unwrap_or_else(|| NaiveDateTime::from_timestamp_opt(i64::MAX / 2, 0).unwrap());
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
        .unwrap_or_else(|| NaiveDateTime::from_timestamp_opt(0, 0).unwrap());
    let end = parse_iso_naive(end_iso)
        .unwrap_or_else(|| NaiveDateTime::from_timestamp_opt(i64::MAX / 2, 0).unwrap());
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
