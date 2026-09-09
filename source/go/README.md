# Go Runtime Guide (Structure + Struct/Method Details)

This document introduces the Go runtime implementation in this folder, with detailed struct/method-level code mapping.

## 1) Folder Structure

```text
go/
├── go.mod
├── server/
│   └── main.go
└── client/
    └── main.go
```

- `server/main.go`: Varlink calculator server over UNIX socket.
- `client/main.go`: benchmark-friendly CLI client.
- `go.mod`: module metadata (`go 1.22`).

---

## 2) Server Structure (`server/main.go`)

The server is organized into these layers:

1. Protocol/constants definitions
2. Data structs (request/response/state/result)
3. `calculator` struct with async-style methods
4. Request dispatching
5. Connection loop (read/decode/dispatch/write)
6. Server entrypoint and signal handling

### 2.1 Constants

- `ServiceName = "xyz.openbmc_project.Calculator"`
- `ObjectPath = "/xyz/openbmc_project/calculator"`
- `varlinkServiceName = "org.varlink.service"`
- `serviceInterfaceDescription`: interface description string returned by introspection method.

### 2.2 Structs (Class-like Models)

#### `rpcRequest`

Fields:
- `Method string`
- `Parameters map[string]any`
- `Oneway bool`

Used by:
- `handleConn` (JSON decode)
- `dispatch` (method routing)

#### `rpcResponse`

Fields:
- `Parameters map[string]any`
- `Error string`

Used by:
- `dispatch` (build response)
- `writeResponseWithDelimiter` (JSON encode + write)

#### `state`

Fields:
- `LastResult int64`
- `Status string`
- `Base string`
- `Owner string`
- `ObjectPath string`
- `Service string`

Used by:
- `getStateAsync` return payload

#### `result[T]` (generic)

Fields:
- `value T`
- `err error`

Used by:
- async-style methods (`multiplyAsync`, `divideAsync`, etc.)
- caller receives `<-chan result[T]`

#### `calculator`

Fields:
- `mu sync.RWMutex`
- `lastResult int64`
- `status string`
- `base string`
- `owner string`

Constructor:
- `newCalculator()`
  - initializes default state (`Success`, `Decimal`, owner `root`).

---

## 3) Server Method-Level Details

### 3.1 Calculator async-style methods

All methods return channels (`<-chan result[T]`) and run logic in goroutines.

- `multiplyAsync(ctx, x, y) <-chan result[int64]`
  - Computes `x*y`, updates internal state.

- `divideAsync(ctx, x, y) <-chan result[int64]`
  - Computes `x/y`; if `y==0` returns `DivisionByZero` error.

- `expressAsync(ctx) <-chan result[string]`
  - Converts `lastResult` by base (`Binary`, `Heximal`, decimal fallback).

- `clearAsync(ctx) <-chan result[struct{}]`
  - Resets result/status.

- `getStateAsync(ctx) <-chan result[state]`
  - Builds full state snapshot including `ObjectPath` and `ServiceName`.

- `setOwnerAsync(ctx, owner) <-chan result[struct{}]`
  - Requires `CALCULATOR_ALLOW_OWNER_CHANGE=1` or returns `PermissionDenied`.

### 3.2 Utility methods

- `toInt64(v any) (int64, bool)`
  - Parses JSON-decoded numeric values into `int64`.

- `readMessage(reader) ([]byte, byte, error)`
  - Reads one message until delimiter (`\n` or `\0`).
  - Returns payload + delimiter.

- `writeResponse(w, resp)`
  - Wrapper that writes with newline delimiter.

- `writeResponseWithDelimiter(w, resp, delimiter)`
  - Serializes and writes response using incoming delimiter.

### 3.3 Request router

- `dispatch(ctx, calc, req) rpcResponse`
  - Central method routing for:
    - `org.varlink.service.GetInfo`
    - `org.varlink.service.GetInterfaceDescription`
    - `xyz.openbmc_project.Calculator.*`
  - Maps parameter errors to `org.varlink.service.InvalidParameter`.
  - Maps unknown methods to `org.varlink.service.MethodNotImplemented`.

### 3.4 Connection handler + entrypoint

- `handleConn(calc, conn)`
  - Loop: read frame → decode JSON → dispatch → optional write response (`!oneway`).
  - Closes connection on EOF/write/read failures.

- `main()`
  - Parses `--socket`.
  - Creates UNIX listener.
  - Initializes shared `calculator`.
  - Handles SIGINT/SIGTERM and closes listener.
  - Accept loop spawns `go handleConn(calc, conn)` per connection.

---

## 4) Client Structure (`client/main.go`)

The client focuses on repeated RPC calls for benchmark usage.

### 4.1 Structs (Class-like Models)

#### `rpcRequest`
- Same request shape as server.

#### `rpcResponse`
- Same response shape as server.

#### `result[T]`
- Async-style return container for method wrappers.

#### `client`

Fields:
- `conn net.Conn`
- `reader *bufio.Reader`

Constructor/helper:
- `dial(socket) (*client, error)`

Methods:

- `close() error`
  - Closes the socket connection.

- `call(req rpcRequest) (rpcResponse, error)`
  - Generic RPC:
    1. marshal request JSON
    2. write one line (`\n`)
    3. read one response line
    4. unmarshal response
    5. return server error as Go `error`

- `multiplyAsync(x, y) <-chan result[int64]`
  - Calls `ServiceName + ".Multiply"`.

- `divideAsync(x, y) <-chan result[int64]`
  - Calls `ServiceName + ".Divide"`.

- `expressAsync() <-chan result[string]`
  - Calls `ServiceName + ".Express"`.

### 4.2 Client entrypoint

- `main()`
  - Parses args:
    - `--socket`
    - `--method` (`Multiply|Divide|Express`)
    - `--x`, `--y`
    - `--iterations`
  - Dials server once.
  - Loops `iterations` and receives from async channels.
  - Prints final summary:
    - `result=<...> elapsed_ms=<...> iterations=<...>`

---

## 5) Struct and Method Relationship Map

### Server flow

1. `main` creates one shared `calculator`.
2. `handleConn` parses each request into `rpcRequest`.
3. `dispatch` routes method and triggers calculator async method.
4. Async method updates internal state under mutex.
5. `dispatch` builds `rpcResponse`.
6. `handleConn` writes response to socket.

### Client flow

1. `main` dials and selects operation.
2. Operation wrapper (`multiplyAsync` etc.) launches goroutine.
3. Wrapper uses `call` to exchange `rpcRequest`/`rpcResponse`.
4. `main` collects final value and prints elapsed benchmark line.

---

## 6) Notes

- Async style is implemented with goroutines + typed result channels.
- Shared mutable state is protected by `sync.RWMutex`.
- Server supports both newline and NUL framing for broader tool compatibility.
- The API surface is aligned with Python/C++/Rust runtimes in this repository.
