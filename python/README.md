# Python Runtime Guide (Structure + Class/Method Details)

This document introduces the Python runtime implementation in this folder, including code structure and class/method-level responsibilities.

## 1) Folder Structure

```text
python/
├── server.py   # asyncio Varlink calculator server
└── client.py   # asyncio CLI client (benchmark-friendly)
```

- `server.py` hosts the service and shared calculator state.
- `client.py` sends Varlink-style requests and supports iteration-based benchmark calls.

---

## 2) Server Structure (`server.py`)

`server.py` can be read in 6 layers:

1. Constants and interface description
2. State model class
3. Service class (business methods)
4. Dispatcher (method routing + error mapping)
5. Connection handler (message framing / I/O loop)
6. Entry point (`main`) for socket server startup

### 2.1 Constants

- `SERVICE_NAME = "xyz.openbmc_project.Calculator"`
- `OBJECT_PATH = "/xyz/openbmc_project/calculator"`
- `VARLINK_SERVICE = "org.varlink.service"`
- `SERVICE_INTERFACE_DESCRIPTION`: introspection description text

### 2.2 Class: `CalculatorState` (dataclass)

Purpose:
- Holds mutable calculator state.

Fields:
- `last_result: int = 0`
- `status: str = "Success"`
- `base: str = "Decimal"`
- `owner: str = "root"`

Used by:
- `Calculator` class methods for all read/write state updates.

### 2.3 Class: `Calculator`

Purpose:
- Implements core calculator business logic.
- Protects shared state with `asyncio.Lock` for concurrency safety.

Constructor:
- `__init__`
  - Creates `self._state: CalculatorState`
  - Creates `self._lock: asyncio.Lock`

Methods:

- `multiply(self, x: int, y: int = 1) -> int`
  - Computes multiplication.
  - Updates `last_result` and `status`.

- `divide(self, x: int, y: int = 1) -> int`
  - Integer division.
  - Raises `ValueError("DivisionByZero")` when `y == 0`.

- `express(self) -> str`
  - Converts `last_result` based on `base`:
    - `Binary` → binary string
    - `Heximal` → hex string
    - default → decimal string

- `clear(self) -> None`
  - Resets `last_result` to 0 and status to success.

- `get_state(self) -> dict`
  - Returns state snapshot with:
    - `lastResult`, `status`, `base`, `owner`
    - `objectPath`, `serviceName`

- `set_owner(self, owner: str) -> None`
  - Requires env var `CALCULATOR_ALLOW_OWNER_CHANGE=1`.
  - Raises `PermissionError("PermissionDenied")` otherwise.

### 2.4 Function: `dispatch(calc: Calculator, request: dict) -> dict`

Purpose:
- Central method router from incoming request to class methods.

Handled methods:

- Varlink service methods:
  - `org.varlink.service.GetInfo`
  - `org.varlink.service.GetInterfaceDescription`

- Calculator methods:
  - `xyz.openbmc_project.Calculator.Multiply` → `calc.multiply`
  - `xyz.openbmc_project.Calculator.Divide` → `calc.divide`
  - `xyz.openbmc_project.Calculator.Express` → `calc.express`
  - `xyz.openbmc_project.Calculator.Clear` → `calc.clear`
  - `xyz.openbmc_project.Calculator.GetState` → `calc.get_state`
  - `xyz.openbmc_project.Calculator.SetOwner` → `calc.set_owner`

Error mapping:
- Missing/invalid parameters → `org.varlink.service.InvalidParameter`
- Unknown method → `org.varlink.service.MethodNotImplemented`
- Divide by zero → `xyz.openbmc_project.Calculator.DivisionByZero`
- Permission denied → `xyz.openbmc_project.Calculator.PermissionDenied`
- Unexpected error → `org.varlink.service.InternalError`

### 2.5 Function: `handle_client(reader, writer, calc)`

Purpose:
- Per-connection async loop for protocol I/O.

Behavior:
- Reads one message at a time (supports newline `\n` and NUL `\0` delimiters).
- Parses JSON request.
- Calls `dispatch`.
- Sends JSON response unless request has `oneway: true`.
- Ensures socket close in `finally`.

### 2.6 Function: `main()`

Purpose:
- CLI entry point for server startup.

Behavior:
- Parses `--socket` argument (default `/tmp/calculator-python.sock`).
- Removes stale socket file.
- Builds shared `Calculator` instance.
- Starts `asyncio.start_unix_server` and serves forever.

---

## 3) Client Structure (`client.py`)

`client.py` provides one class plus run/entry orchestration.

### 3.1 Class: `VarlinkClient`

Purpose:
- Encapsulates connection lifecycle and calculator RPC calls.

Constructor:
- `__init__(socket_path: str)`
  - Initializes socket path and placeholders for reader/writer.

Methods:

- `connect(self) -> None`
  - Opens UNIX socket connection and stores stream handles.

- `close(self) -> None`
  - Gracefully closes connection if opened.

- `_call(self, method: str, parameters: dict | None = None) -> dict`
  - Internal generic RPC method.
  - Sends request with full method name `${SERVICE_NAME}.{method}`.
  - Reads line-based JSON response.
  - Raises `RuntimeError` when `error` field is present.
  - Returns `parameters` object from response.

- `multiply(self, x: int, y: int = 1) -> int`
  - Calls `_call("Multiply", {...})`, returns `z` as int.

- `divide(self, x: int, y: int = 1) -> int`
  - Calls `_call("Divide", {...})`, returns `z` as int.

- `express(self) -> str`
  - Calls `_call("Express")`, returns `z` as string.

### 3.2 Function: `run(args)`

Purpose:
- Benchmark-oriented execution loop.

Behavior:
- Connects client.
- Repeats selected method for `args.iterations`.
- Measures elapsed time.
- Prints final summary line:
  - `result=<...> elapsed_ms=<...> iterations=<...>`

### 3.3 Function: `main()`

Purpose:
- CLI parser + async launcher.

Arguments:
- `--socket`
- `--method` (`Multiply`, `Divide`, `Express`)
- `--x`, `--y`
- `--iterations`

Then calls `asyncio.run(run(args))`.

---

## 4) Class and Method Relationship Map

### Server-side flow

1. `main` creates one shared `Calculator` instance.
2. Each connection is processed by `handle_client`.
3. `handle_client` decodes request and calls `dispatch`.
4. `dispatch` routes to a `Calculator` method.
5. `Calculator` method updates/reads `CalculatorState`.
6. `dispatch` returns protocol response dict.
7. `handle_client` writes response back to client.

### Client-side flow

1. `main` parses CLI args and starts `run`.
2. `run` creates `VarlinkClient` and connects.
3. `run` repeatedly invokes method wrappers (`multiply/divide/express`).
4. Wrappers call `_call` for protocol roundtrip.
5. `run` prints final benchmark-style output.

---

## 5) Notes

- Async model is based on `asyncio` and UNIX domain sockets.
- Shared state correctness is protected by `asyncio.Lock`.
- Message framing supports both newline and NUL terminators for better tool compatibility.
- This runtime mirrors the same functional API surface as other runtimes in this repository.
