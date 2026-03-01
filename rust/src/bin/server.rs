use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::env;
use std::io;
use std::sync::Arc;
use tokio::io::{AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::{UnixListener, UnixStream};
use tokio::sync::Mutex;

const SERVICE_NAME: &str = "xyz.openbmc_project.Calculator";
const OBJECT_PATH: &str = "/xyz/openbmc_project/calculator";
const VARLINK_SERVICE: &str = "org.varlink.service";
const SERVICE_INTERFACE_DESCRIPTION: &str = r#"interface xyz.openbmc_project.Calculator

type State (
    lastResult: int,
    status: string,
    base: string,
    owner: string,
    objectPath: string,
    serviceName: string
)

method Multiply(x: int, y: ?int) -> (z: int)
method Divide(x: int, y: ?int) -> (z: int)
method Express() -> (z: string)
method Clear() -> ()
method GetState() -> (state: State)
method SetOwner(owner: string) -> ()

error DivisionByZero ()
error PermissionDenied ()
"#;

#[derive(Debug, Deserialize)]
struct RpcRequest {
    method: String,
    #[serde(default)]
    parameters: HashMap<String, Value>,
    #[serde(default)]
    oneway: bool,
}

#[derive(Debug, Serialize)]
struct RpcResponse {
    #[serde(skip_serializing_if = "Option::is_none")]
    parameters: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

#[derive(Debug, Clone)]
struct CalculatorState {
    last_result: i64,
    status: String,
    base: String,
    owner: String,
}

impl Default for CalculatorState {
    fn default() -> Self {
        Self {
            last_result: 0,
            status: "Success".to_string(),
            base: "Decimal".to_string(),
            owner: "root".to_string(),
        }
    }
}

async fn multiply(state: Arc<Mutex<CalculatorState>>, x: i64, y: i64) -> i64 {
    let mut guard = state.lock().await;
    let z = x * y;
    guard.last_result = z;
    guard.status = "Success".to_string();
    z
}

async fn divide(state: Arc<Mutex<CalculatorState>>, x: i64, y: i64) -> Result<i64, String> {
    let mut guard = state.lock().await;
    if y == 0 {
        guard.status = "Error".to_string();
        return Err(format!("{}.DivisionByZero", SERVICE_NAME));
    }
    let z = x / y;
    guard.last_result = z;
    guard.status = "Success".to_string();
    Ok(z)
}

async fn express(state: Arc<Mutex<CalculatorState>>) -> String {
    let guard = state.lock().await;
    match guard.base.as_str() {
        "Binary" => format!("{:b}", guard.last_result),
        "Heximal" => format!("{:x}", guard.last_result),
        _ => guard.last_result.to_string(),
    }
}

async fn clear(state: Arc<Mutex<CalculatorState>>) {
    let mut guard = state.lock().await;
    guard.last_result = 0;
    guard.status = "Success".to_string();
}

async fn get_state(state: Arc<Mutex<CalculatorState>>) -> Value {
    let guard = state.lock().await;
    json!({
        "lastResult": guard.last_result,
        "status": guard.status,
        "base": guard.base,
        "owner": guard.owner,
        "objectPath": OBJECT_PATH,
        "serviceName": SERVICE_NAME,
    })
}

async fn set_owner(state: Arc<Mutex<CalculatorState>>, owner: String) -> Result<(), String> {
    if env::var("CALCULATOR_ALLOW_OWNER_CHANGE").unwrap_or_default() != "1" {
        let mut guard = state.lock().await;
        guard.status = "Error".to_string();
        return Err(format!("{}.PermissionDenied", SERVICE_NAME));
    }

    let mut guard = state.lock().await;
    guard.owner = owner;
    guard.status = "Success".to_string();
    Ok(())
}

fn bad_param() -> RpcResponse {
    RpcResponse {
        parameters: None,
        error: Some("org.varlink.service.InvalidParameter".to_string()),
    }
}

async fn dispatch(state: Arc<Mutex<CalculatorState>>, req: RpcRequest) -> RpcResponse {
    let method = req.method.as_str();
    match method {
        "org.varlink.service.GetInfo" => RpcResponse {
            parameters: Some(json!({
                "vendor": "GoVarlink",
                "product": SERVICE_NAME,
                "version": "0.1.0",
                "url": "https://github.com/JohnBlue-git/GoVarlink",
                "interfaces": [SERVICE_NAME],
            })),
            error: None,
        },
        "org.varlink.service.GetInterfaceDescription" => {
            let iface = match req.parameters.get("interface").and_then(|v| v.as_str()) {
                Some(v) => v,
                None => return bad_param(),
            };
            if iface != SERVICE_NAME {
                return RpcResponse {
                    parameters: None,
                    error: Some("org.varlink.service.InterfaceNotFound".to_string()),
                };
            }
            RpcResponse {
                parameters: Some(json!({ "description": SERVICE_INTERFACE_DESCRIPTION })),
                error: None,
            }
        }
        "xyz.openbmc_project.Calculator.Multiply" => {
            let x = match req.parameters.get("x").and_then(|v| v.as_i64()) {
                Some(v) => v,
                None => return bad_param(),
            };
            let y = req.parameters.get("y").and_then(|v| v.as_i64()).unwrap_or(1);
            let z = multiply(state, x, y).await;
            RpcResponse {
                parameters: Some(json!({ "z": z })),
                error: None,
            }
        }
        "xyz.openbmc_project.Calculator.Divide" => {
            let x = match req.parameters.get("x").and_then(|v| v.as_i64()) {
                Some(v) => v,
                None => return bad_param(),
            };
            let y = req.parameters.get("y").and_then(|v| v.as_i64()).unwrap_or(1);
            match divide(state, x, y).await {
                Ok(z) => RpcResponse {
                    parameters: Some(json!({ "z": z })),
                    error: None,
                },
                Err(err) => RpcResponse {
                    parameters: None,
                    error: Some(err),
                },
            }
        }
        "xyz.openbmc_project.Calculator.Express" => {
            let z = express(state).await;
            RpcResponse {
                parameters: Some(json!({ "z": z })),
                error: None,
            }
        }
        "xyz.openbmc_project.Calculator.Clear" => {
            clear(state).await;
            RpcResponse {
                parameters: Some(json!({})),
                error: None,
            }
        }
        "xyz.openbmc_project.Calculator.GetState" => {
            let st = get_state(state).await;
            RpcResponse {
                parameters: Some(json!({ "state": st })),
                error: None,
            }
        }
        "xyz.openbmc_project.Calculator.SetOwner" => {
            let owner = match req.parameters.get("owner").and_then(|v| v.as_str()) {
                Some(v) => v.to_string(),
                None => return bad_param(),
            };
            match set_owner(state, owner).await {
                Ok(()) => RpcResponse {
                    parameters: Some(json!({})),
                    error: None,
                },
                Err(err) => RpcResponse {
                    parameters: None,
                    error: Some(err),
                },
            }
        }
        _ => RpcResponse {
            parameters: None,
            error: Some("org.varlink.service.MethodNotImplemented".to_string()),
        },
    }
}

async fn read_message(
    reader: &mut BufReader<tokio::net::unix::OwnedReadHalf>,
) -> io::Result<Option<(Vec<u8>, u8)>> {
    let mut payload = Vec::with_capacity(512);
    loop {
        match reader.read_u8().await {
            Ok(b) if b == b'\n' || b == 0 => return Ok(Some((payload, b))),
            Ok(b) => payload.push(b),
            Err(err) if err.kind() == io::ErrorKind::UnexpectedEof => {
                if payload.is_empty() {
                    return Ok(None);
                }
                return Ok(Some((payload, b'\n')));
            }
            Err(err) => return Err(err),
        }
    }
}

async fn write_response(
    writer: &mut tokio::net::unix::OwnedWriteHalf,
    resp: &RpcResponse,
    delimiter: u8,
) -> io::Result<()> {
    let payload = serde_json::to_vec(resp).unwrap_or_else(|_| {
        b"{\"error\":\"org.varlink.service.InternalError\"}".to_vec()
    });
    writer.write_all(&payload).await?;
    writer.write_all(&[delimiter]).await
}

async fn handle_client(stream: UnixStream, state: Arc<Mutex<CalculatorState>>) {
    let (reader_half, mut writer_half) = stream.into_split();
    let mut reader = BufReader::new(reader_half);

    loop {
        let (raw, delimiter) = match read_message(&mut reader).await {
            Ok(Some(v)) => v,
            Ok(None) => break,
            Err(_) => break,
        };

        let req = match serde_json::from_slice::<RpcRequest>(&raw) {
            Ok(r) => r,
            Err(_) => {
                let resp = RpcResponse {
                    parameters: None,
                    error: Some("org.varlink.service.InvalidParameter".to_string()),
                };
                if write_response(&mut writer_half, &resp, delimiter).await.is_err() {
                    break;
                }
                continue;
            }
        };

        let oneway = req.oneway;
        let resp = dispatch(state.clone(), req).await;
        if oneway {
            continue;
        }

        if write_response(&mut writer_half, &resp, delimiter).await.is_err() {
            break;
        }
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut socket_path = String::from("/tmp/calculator-rust.sock");
    let args: Vec<String> = env::args().collect();
    let mut idx = 1;
    while idx + 1 < args.len() {
        if args[idx] == "--socket" {
            socket_path = args[idx + 1].clone();
            idx += 2;
            continue;
        }
        idx += 1;
    }

    let _ = std::fs::remove_file(&socket_path);
    let listener = UnixListener::bind(&socket_path)?;
    eprintln!("Rust calculator server on {}", socket_path);

    let state = Arc::new(Mutex::new(CalculatorState::default()));

    loop {
        let (stream, _) = listener.accept().await?;
        let state_clone = state.clone();
        tokio::spawn(async move {
            handle_client(stream, state_clone).await;
        });
    }
}
