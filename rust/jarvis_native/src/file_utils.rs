use pyo3::prelude::*;
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use pyo3::types::{PyDict, PyTuple};
use pyo3::PyObject;


#[pyfunction]
pub fn get_file_md5(filepath: &str) -> PyResult<String> {
    let limit: u64 = 100 * 1024 * 1024;
    let mut f = match File::open(filepath) {
        Ok(h) => h,
        Err(_) => return Ok(String::new()),
    };
    let mut ctx = md5::Context::new();
    let mut buf = vec![0u8; 8 * 1024 * 1024];
    let mut read_total: u64 = 0;
    loop {
        let to_read = std::cmp::min(buf.len() as u64, limit.saturating_sub(read_total)) as usize;
        if to_read == 0 {
            break;
        }
        match f.read(&mut buf[..to_read]) {
            Ok(0) => break,
            Ok(n) => {
                ctx.consume(&buf[..n]);
                read_total += n as u64;
            }
            Err(_) => break,
        }
    }
    let digest = ctx.compute();
    Ok(format!("{:x}", digest))
}

#[pyfunction]
pub fn get_file_line_count(filepath: &str) -> PyResult<u64> {
    let file = match File::open(filepath) {
        Ok(f) => f,
        Err(_) => return Ok(0),
    };
    let reader = BufReader::new(file);
    let mut count: u64 = 0;
    for line in reader.lines() {
        if line.is_err() {
            return Ok(0);
        }
        count += 1;
    }
    Ok(count)
}

#[pyfunction]
pub fn list_git_files() -> PyResult<Vec<String>> {
    use std::process::Command;
    let out = match Command::new("git").arg("ls-files").output() {
        Ok(o) => o,
        Err(_) => return Ok(Vec::new()),
    };
    if !out.status.success() {
        return Ok(Vec::new());
    }
    let stdout = String::from_utf8_lossy(&out.stdout);
    let mut v: Vec<String> = stdout.lines().map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect();
    v.shrink_to_fit();
    Ok(v)
}

#[pyfunction]
pub fn list_files_excluding_git(root: &str, max: usize) -> PyResult<Vec<String>> {
    let root_path = Path::new(root);
    let mut result: Vec<String> = Vec::new();
    let mut stack: Vec<PathBuf> = vec![root_path.to_path_buf()];

    while let Some(dir) = stack.pop() {
        let read = match fs::read_dir(&dir) {
            Ok(r) => r,
            Err(_) => continue,
        };
        for entry in read.flatten() {
            let p = entry.path();
            let name = p.file_name().and_then(|s| s.to_str()).unwrap_or("");
            if name == ".git" {
                continue;
            }
            if p.is_dir() {
                stack.push(p);
            } else if p.is_file() {
                let rel = if let Ok(relp) = p.strip_prefix(root_path) {
                    relp.to_string_lossy().to_string()
                } else {
                    p.to_string_lossy().to_string()
                };
                result.push(rel);
                if result.len() >= max {
                    return Ok(result);
                }
            }
        }
    }
    Ok(result)
}

#[pyfunction]
pub fn git_is_file_in_repo(filepath: &str) -> PyResult<bool> {
    use std::process::Command;
    // get repo root
    let out = Command::new("git")
        .args(&["rev-parse", "--show-toplevel"])
        .output();
    let root = match out {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).trim().to_string(),
        _ => return Ok(false),
    };
    if root.is_empty() {
        return Ok(false);
    }
    // canonicalize both paths
    let repo_root = Path::new(&root);
    let file_path = Path::new(filepath);
    let repo_can = match fs::canonicalize(repo_root) {
        Ok(p) => p,
        Err(_) => return Ok(false),
    };
    let file_can = match fs::canonicalize(file_path) {
        Ok(p) => p,
        Err(_) => return Ok(false),
    };
    Ok(file_can.starts_with(&repo_can))
}

#[pyfunction]
pub fn git_get_commits_between(py: Python<'_>, start: &str, end: &str) -> PyResult<Vec<PyObject>> {
    use std::process::Command;
    let out = match Command::new("git")
        .args(&["log", &format!("{}..{}", start, end), "--pretty=format:%H|%s"])
        .output()
    {
        Ok(o) => o,
        Err(_) => return Ok(Vec::new()),
    };
    if !out.status.success() {
        return Ok(Vec::new());
    }
    let stdout = String::from_utf8_lossy(&out.stdout);
    let mut res: Vec<PyObject> = Vec::new();
    for line in stdout.lines() {
        if let Some(idx) = line.find('|') {
            let (h, m) = line.split_at(idx);
            let msg = &m[1..];
            let h_str = pyo3::types::PyString::new_bound(py, h);
            let m_str = pyo3::types::PyString::new_bound(py, msg);
            let tup = PyTuple::new_bound(
                py,
                &[
                    h_str.into_any().unbind(),
                    m_str.into_any().unbind(),
                ],
            );
            res.push(tup.into_any().unbind());
        }
    }
    Ok(res)
}

#[pyfunction]
pub fn git_has_uncommitted_changes() -> PyResult<bool> {
    use std::process::Command;
    // git add .
    let _ = Command::new("git")
        .args(&["add", "."])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status();
    // working tree changes
    let working = Command::new("git")
        .args(&["diff", "--exit-code"])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| !s.success())
        .unwrap_or(false);
    // staged changes
    let staged = Command::new("git")
        .args(&["diff", "--cached", "--exit-code"])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| !s.success())
        .unwrap_or(false);
    // git reset
    let _ = Command::new("git")
        .arg("reset")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status();
    Ok(working || staged)
}

#[pyfunction]
pub fn git_get_diff_file_list() -> PyResult<Vec<String>> {
    use std::process::{Command, Stdio};
    // stage intent for new files
    let _ = Command::new("git")
        .args(&["add", "-N", "."])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
    // diff name-only against HEAD
    let out = Command::new("git")
        .args(&["diff", "--name-only", "HEAD"])
        .output();
    // reset staging area
    let _ = Command::new("git")
        .arg("reset")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();

    let output = match out {
        Ok(o) => o,
        Err(_) => return Ok(Vec::new()),
    };
    if !output.status.success() {
        return Ok(Vec::new());
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    let files: Vec<String> = stdout
        .lines()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();
    Ok(files)
}

#[pyfunction]
pub fn git_list_untracked_files() -> PyResult<Vec<String>> {
    use std::process::Command;
    let out = match Command::new("git")
        .args(&["ls-files", "--others", "--exclude-standard"])
        .output()
    {
        Ok(o) => o,
        Err(_) => return Ok(Vec::new()),
    };
    if !out.status.success() {
        return Ok(Vec::new());
    }
    let stdout = String::from_utf8_lossy(&out.stdout);
    let files: Vec<String> = stdout
        .lines()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();
    Ok(files)
}

#[pyfunction]
pub fn git_find_root(start_dir: &str, init_if_needed: bool) -> PyResult<String> {
    use std::process::{Command, Stdio};

    // Try to get toplevel with provided start_dir
    let out = Command::new("git")
        .args(&["rev-parse", "--show-toplevel"])
        .current_dir(start_dir)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output();

    match out {
        Ok(o) if o.status.success() => {
            let root = String::from_utf8_lossy(&o.stdout).trim().to_string();
            Ok(root)
        }
        _ => {
            if init_if_needed {
                // initialize git repo at start_dir
                let _ = Command::new("git")
                    .arg("init")
                    .current_dir(start_dir)
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status();
                // return canonicalized start_dir
                match fs::canonicalize(start_dir) {
                    Ok(p) => Ok(p.to_string_lossy().to_string()),
                    Err(_) => Ok(start_dir.to_string()),
                }
            } else {
                Ok(String::new())
            }
        }
    }
}

#[pyfunction]
pub fn git_get_latest_commit_hash() -> PyResult<String> {
    use std::process::Command;
    let check = Command::new("git")
        .args(&["rev-parse", "--verify", "HEAD"])
        .output();
    match check {
        Ok(c) if c.status.success() => {
            let out = Command::new("git").args(&["rev-parse", "HEAD"]).output();
            match out {
                Ok(o) if o.status.success() => {
                    Ok(String::from_utf8_lossy(&o.stdout).trim().to_string())
                }
                _ => Ok(String::new()),
            }
        }
        _ => Ok(String::new()),
    }
}

#[pyfunction(signature = (limit, author=None))]
pub fn git_recent_commits_with_files(py: Python<'_>, limit: usize, author: Option<&str>) -> PyResult<Vec<PyObject>> {
    use std::process::Command;

    // Build git args
    let mut args: Vec<String> = Vec::new();
    args.push("log".to_string());
    args.push(format!("-{}", limit));
    if let Some(a) = author {
        if !a.trim().is_empty() {
            args.push(format!("--author={}", a));
        }
    }
    // header fields separated by \x1f, record separated by \x1e
    args.push("--pretty=format:%H\x1f%s\x1f%an\x1f%ad\x1e".to_string());
    args.push("--name-only".to_string());

    let output = match Command::new("git").args(&args).output() {
        Ok(o) => o,
        Err(_) => return Ok(Vec::new()),
    };
    if !output.status.success() {
        return Ok(Vec::new());
    }
    let text = String::from_utf8_lossy(&output.stdout);

    let mut result: Vec<PyObject> = Vec::new();
    for rec in text.split('\x1e') {
        let rec = rec.trim_end();
        if rec.is_empty() {
            continue;
        }
        let mut lines = rec.lines();

        // header line
        let header = match lines.next() {
            Some(h) => h,
            None => continue,
        };
        let mut it = header.split('\x1f');
        let hash = match it.next() { Some(s) => s, None => continue };
        let message = match it.next() { Some(s) => s, None => "" };
        let author = match it.next() { Some(s) => s, None => "" };
        let date = match it.next() { Some(s) => s, None => "" };

        // files
        let mut files: Vec<String> = Vec::new();
        for l in lines {
            let l = l.trim();
            if !l.is_empty() {
                files.push(l.to_string());
            }
        }
        // unique and cap to 20
        files.sort();
        files.dedup();
        if files.len() > 20 {
            files.truncate(20);
        }

        let d = PyDict::new_bound(py);
        d.set_item("hash", hash).ok();
        d.set_item("message", message).ok();
        d.set_item("author", author).ok();
        d.set_item("date", date).ok();
        d.set_item("files", files).ok();

        result.push(d.into_any().unbind());
    }

    Ok(result)
}
