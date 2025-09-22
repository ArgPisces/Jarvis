use pyo3::prelude::*;
use std::fs::File;
use std::io::{BufRead, BufReader, Read};

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
