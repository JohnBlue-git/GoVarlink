use serde::Deserialize;
use serde_json::json;
use std::env;
use std::time::Instant;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixStream;

const SERVICE_NAME: &str = "xyz.openbmc_project.Calculator";

#[derive(Debug, Deserialize)]
struct RpcResponse {
    #[serde(default)]
    parameters: serde_json::Value,
    #[serde(default)]
    error: String,
}

async fn call(
    writer: &mut tokio::net::unix::OwnedWriteHalf,
    reader: &mut BufReader<tokio::net::unix::OwnedReadHalf>,
    method: &str,
    parameters: serde_json::Value,
) -> Result<RpcResponse, Box<dyn std::error::Error>> {
    let req = json!({
        "method": format!("{}.{}", SERVICE_NAME, method),
        "parameters": parameters,
    });

    let req_line = format!("{}\n", serde_json::to_string(&req)?);
    writer.write_all(req_line.as_bytes()).await?;

    let mut line = String::new();
    reader.read_line(&mut line).await?;
    let response: RpcResponse = serde_json::from_str(line.trim_end())?;
    if !response.error.is_empty() {
        return Err(response.error.into());
    }
    Ok(response)
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut socket = String::from("/tmp/calculator-rust.sock");
    let mut method = String::from("Multiply");
    let mut x: i64 = 7;
    let mut y: i64 = 3;
    let mut iterations: usize = 1;

    let args: Vec<String> = env::args().collect();
    let mut idx = 1;
    while idx + 1 < args.len() {
        match args[idx].as_str() {
            "--socket" => socket = args[idx + 1].clone(),
            "--method" => method = args[idx + 1].clone(),
            "--x" => x = args[idx + 1].parse().unwrap_or(7),
            "--y" => y = args[idx + 1].parse().unwrap_or(3),
            "--iterations" => iterations = args[idx + 1].parse().unwrap_or(1),
            _ => {}
        }
        idx += 2;
    }

    let stream = UnixStream::connect(socket).await?;
    let (read_half, mut write_half) = stream.into_split();
    let mut reader = BufReader::new(read_half);

    let start = Instant::now();
    let mut result_string = String::new();

    for _ in 0..iterations {
        let resp = match method.as_str() {
            "Multiply" => call(&mut write_half, &mut reader, "Multiply", json!({ "x": x, "y": y })).await?,
            "Divide" => call(&mut write_half, &mut reader, "Divide", json!({ "x": x, "y": y })).await?,
            "Express" => call(&mut write_half, &mut reader, "Express", json!({})).await?,
            _ => return Err(format!("unsupported method: {}", method).into()),
        };

        if method == "Express" {
            result_string = resp
                .parameters
                .get("z")
                .and_then(|v| v.as_str())
                .unwrap_or_default()
                .to_string();
        } else {
            let z = resp.parameters.get("z").and_then(|v| v.as_i64()).unwrap_or_default();
            result_string = z.to_string();
        }
    }

    let elapsed_ms = start.elapsed().as_millis();
    println!(
        "result={} elapsed_ms={} iterations={}",
        result_string, elapsed_ms, iterations
    );

    Ok(())
}
