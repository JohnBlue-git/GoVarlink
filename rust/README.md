# Rust Runtime Guide (Structure + Struct/Method Details)

This document introduces the Rust implementation structure for the Varlink calculator runtime in this folder.

## 1) Folder Structure

```text
rust/
├── Cargo.toml
├── Cargo.lock
└── src/
    └── bin/
        ├── server.rs   # Varlink server
        └── client.rs   # benchmark-friendly CLI client
```

- `Cargo.toml` defines one package and two binaries:
  - `calculator_server` → `src/bin/server.rs`
  - `calculator_client` → `src/bin/client.rs`
- Main dependencies:
  - `tokio` for async runtime + UNIX socket I/O
  - `serde` + `serde_json` for JSON serialization/deserialization

---

## 2) Server Code Structure (`src/bin/server.rs`)

The server is organized into constants, data structs, domain methods, protocol helpers, and socket loop.

### 2.1 Constants

- `SERVICE_NAME`: `xyz.openbmc_project.Calculator`
- `OBJECT_PATH`: `/xyz/openbmc_project/calculator`
- `VARLINK_SERVICE`: `org.varlink.service` (defined for service namespace consistency)
- `SERVICE_INTERFACE_DESCRIPTION`: interface text returned by introspection-style API

### 2.2 Structs (Class-like Data Models)

#### `RpcRequest`

Purpose:
- Represents one incoming Varlink request message.

Fields:
- `method: String`
- `parameters: HashMap<String, Value>` (default empty)
- `oneway: bool` (default `false`)

Related methods/functions:
- Parsed from raw socket payload in `handle_client`.
- Consumed by `dispatch` to route method handling.

#### `RpcResponse`

Purpose:
- Represents one outgoing response.

Fields:
- `parameters: Option<Value>`
- `error: Option<String>`

Related methods/functions:
- Created in `dispatch`, `bad_param`, and decode-error branch in `handle_client`.
- Serialized and written via `write_response`.

#### `CalculatorState`

Purpose:
- Shared mutable service state.

Fields:
- `last_result: i64`
- `status: String`
- `base: String`
- `owner: String`

Traits/impl:
- `Clone`, `Debug`
- `Default` implementation initializes:
  - `last_result = 0`
  - `status = "Success"`
  - `base = "Decimal"`
  - `owner = "root"`

Runtime container:
- Stored as `Arc<Mutex<CalculatorState>>` so each client task can safely read/write shared state.

---

## 3) Server Method-Level Detail

### 3.1 Domain methods (calculator behavior)

- `multiply(state, x, y) -> i64`
  - Updates `last_result` and `status`.
- `divide(state, x, y) -> Result<i64, String>`
  - On `y == 0`, sets error status and returns `DivisionByZero` error name.
- `express(state) -> String`
  - Converts `last_result` by `base` (`Binary`, `Heximal`, default decimal).
- `clear(state)`
  - Resets result and status.
- `get_state(state) -> Value`
  - Produces full state object including `objectPath` and `serviceName`.
- `set_owner(state, owner) -> Result<(), String>`
  - Requires env `CALCULATOR_ALLOW_OWNER_CHANGE=1`, else returns `PermissionDenied`.

### 3.2 Protocol/validation methods

- `bad_param() -> RpcResponse`
  - Returns `org.varlink.service.InvalidParameter` convenience response.

- `dispatch(state, req) -> RpcResponse`
  - Central method router.
  - Handles:
    - `org.varlink.service.GetInfo`
    - `org.varlink.service.GetInterfaceDescription`
    - `xyz.openbmc_project.Calculator.*` methods
  - Converts domain results/errors into Varlink-style response objects.

### 3.3 Socket framing and I/O methods

- `read_message(reader) -> io::Result<Option<(Vec<u8>, u8)>>`
  - Reads one message until delimiter.
  - Supports both `\n` and NUL (`\0`) delimiters.
  - Returns payload bytes + delimiter used.

- `write_response(writer, resp, delimiter) -> io::Result<()>`
  - Serializes `RpcResponse` as JSON and writes back with same delimiter.
  - Falls back to `InternalError` JSON if serialization fails.

- `handle_client(stream, state)`
  - Per-connection async loop.
  - Steps:
    1. read one framed request
    2. decode JSON into `RpcRequest`
    3. call `dispatch`
    4. skip response if `oneway`
    5. write framed response

### 3.4 Entry point

- `main()`
  - Parses `--socket` arg (default `/tmp/calculator-rust.sock`).
  - Removes stale socket file.
  - Binds `UnixListener`.
  - Initializes shared `CalculatorState`.
  - Accept loop + `tokio::spawn` per client connection.

---

## 4) Client Code Structure (`src/bin/client.rs`)

The client is optimized for benchmark execution and repeat calls.

### 4.1 Structs (Class-like models)

#### `RpcResponse`

Purpose:
- Minimal client-side response shape.

Fields:
- `parameters: serde_json::Value`
- `error: String`

Related methods/functions:
- Produced by `call` after JSON decode.
- Consumed in `main` to extract `z` output.

### 4.2 Method-Level Detail

- `call(writer, reader, method, parameters) -> Result<RpcResponse, Error>`
  - Builds request JSON using `SERVICE_NAME + method`.
  - Sends one line-delimited request (`\n`).
  - Reads one response line.
  - Returns error if response `error` field is non-empty.

- `main()`
  - Parses CLI args:
    - `--socket`
    - `--method` (`Multiply` / `Divide` / `Express`)
    - `--x`, `--y`
    - `--iterations`
  - Connects UNIX socket.
  - Loops for `iterations`, repeatedly calling selected method.
  - Extracts final output string from `parameters.z`.
  - Prints `result`, `elapsed_ms`, `iterations`.

---

## 5) Struct and Method Relationship Map

### Server side flow

1. `main` accepts socket connection.
2. `handle_client` reads raw frame.
3. Raw JSON → `RpcRequest`.
4. `dispatch` selects target method.
5. Domain methods mutate/read `CalculatorState`.
6. Result converted to `RpcResponse`.
7. `write_response` sends framed JSON reply.

### Client side flow

1. `main` parses args and opens socket.
2. `call` sends request and decodes `RpcResponse`.
3. `main` loops for benchmarking and prints final summary.

---

## 6) Key Design Notes

- Rust implementation uses async UNIX sockets with Tokio.
- Shared mutable state is synchronized via `Arc<Mutex<CalculatorState>>`.
- Request framing supports both newline and NUL terminators for tool compatibility.
- Server implements calculator API plus basic `org.varlink.service` metadata/introspection methods.
