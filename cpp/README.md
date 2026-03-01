# C++ Runtime Guide (Structure + Class/Method Details)

This document introduces the C++ runtime implementation in this folder, including detailed class and method responsibilities.

## 1) Folder Structure

```text
cpp/
├── Makefile
├── server.cpp
└── client.cpp
```

- `Makefile`: builds both server and client into `cpp/build/`.
- `server.cpp`: Varlink calculator server over UNIX domain socket.
- `client.cpp`: CLI client for single-run and benchmark-style loops.

---

## 2) Build Structure (`Makefile`)

Key targets:

- `all`: builds both `build/calculator_server` and `build/calculator_client`
- `clean`: removes `build/`

Key variables:

- `CXX` (default `g++`)
- `CXXFLAGS` (default `-std=c++20 -O2 -Wall -Wextra -pedantic`)
- `LDFLAGS`

Build commands:

```bash
make -C cpp
```

Clean commands:

```bash
make -C cpp clean
```

---

## 3) Server Code Structure (`server.cpp`)

The server is organized into constants, coroutine task abstraction, state/business classes, request parsing helpers, request dispatcher, and socket serving loop.

### 3.1 Constants

- `SERVICE_NAME = "xyz.openbmc_project.Calculator"`
- `OBJECT_PATH = "/xyz/openbmc_project/calculator"`
- `VARLINK_SERVICE = "org.varlink.service"`
- `SERVICE_INTERFACE_DESCRIPTION`: varlink interface text returned by service introspection call.

### 3.2 Class: `Task<T>` (Coroutine Wrapper)

Purpose:
- Lightweight coroutine return container used by async-style methods.

Important members:
- `promise_type`
  - `value`, `exception`
  - `get_return_object`, `initial_suspend`, `final_suspend`, `return_value`, `unhandled_exception`
- `Task::get()`
  - Retrieves final value or rethrows captured exception.

Used by:
- `Calculator` async methods (`multiply_async`, `divide_async`, etc.)

### 3.3 Struct: `State`

Purpose:
- Holds calculator mutable state.

Fields:
- `lastResult`
- `status`
- `base`
- `owner`

Used by:
- `Calculator` internal state storage.

### 3.4 Class: `Calculator`

Purpose:
- Implements service business logic.
- Protects shared state with `std::mutex`.

Methods:

- `multiply_async(int64_t x, int64_t y) -> Task<int64_t>`
  - Computes product, updates `lastResult/status`.

- `divide_async(int64_t x, int64_t y) -> Task<int64_t>`
  - Division with zero-check.
  - Throws `std::runtime_error("DivisionByZero")` on invalid divisor.

- `express_async() -> Task<std::string>`
  - Formats `lastResult` by `base`:
    - `Binary` via helper conversion
    - `Heximal` via hex stream
    - default decimal

- `clear_async() -> Task<std::string>`
  - Resets result and status.
  - Returns `{}` payload string.

- `get_state_async() -> Task<std::string>`
  - Builds JSON string containing state + `objectPath` + `serviceName`.

- `set_owner_async(const std::string& owner) -> Task<std::string>`
  - Requires env `CALCULATOR_ALLOW_OWNER_CHANGE=1`.
  - Throws `std::runtime_error("PermissionDenied")` if not allowed.

Private helper:
- `to_binary(int64_t)` converts integer to binary string.

Synchronization:
- All state accesses are guarded by `std::lock_guard<std::mutex>`.

### 3.5 Parsing Helper Functions

- `extract_int(body, key) -> std::optional<int64_t>`
- `extract_string(body, key) -> std::optional<std::string>`
- `extract_method(body) -> std::optional<std::string>`

These perform lightweight JSON field extraction from request text.

### 3.6 Response/Dispatch Functions

- `make_error(err) -> std::string`
  - Builds JSON error response string.

- `handle_request(Calculator& calc, const std::string& line) -> std::string`
  - Central request router.
  - Handles:
    - `org.varlink.service.GetInfo`
    - `org.varlink.service.GetInterfaceDescription`
    - `xyz.openbmc_project.Calculator.Multiply`
    - `xyz.openbmc_project.Calculator.Divide`
    - `xyz.openbmc_project.Calculator.Express`
    - `xyz.openbmc_project.Calculator.Clear`
    - `xyz.openbmc_project.Calculator.GetState`
    - `xyz.openbmc_project.Calculator.SetOwner`
  - Maps errors to Varlink-style names:
    - invalid parameter
    - method not implemented
    - division/permission errors
    - internal error

### 3.7 Socket Serving Functions

- `serve_connection(int fd, Calculator& calc)`
  - Reads from connected socket.
  - Supports both newline (`\n`) and NUL (`\0`) framed messages.
  - Calls `handle_request` per message and writes response with matching delimiter.

- `main(int argc, char* argv[])`
  - Parses `--socket`.
  - Removes stale socket file.
  - Creates/binds/listens UNIX socket.
  - Accept loop spawns detached `std::thread` per client using shared `Calculator` instance.

---

## 4) Client Code Structure (`client.cpp`)

The client provides coroutine-style method wrappers over a synchronous socket request/response core.

### 4.1 Class: `Task<T>`

- Same coroutine wrapper pattern as server.
- `Task::get()` extracts value or throws exception.

### 4.2 Helper Functions

- `extract_string(body, key)` and `extract_int(body, key)`
  - Parse response JSON fields.

### 4.3 Class: `Client`

Purpose:
- Manages UNIX socket connection and RPC calls.

Fields:
- `fd_`: socket file descriptor
- `socket_path_`: server socket path

Methods:

- `connect_socket()`
  - Creates and connects UNIX socket.

- `~Client()`
  - Closes socket descriptor when object is destroyed.

- `multiply_async(x, y) -> Task<int64_t>`
  - Builds request JSON for `Multiply`, sends call, parses `z`.

- `divide_async(x, y) -> Task<int64_t>`
  - Builds request JSON for `Divide`, sends call, parses `z`.

- `express_async() -> Task<std::string>`
  - Builds request JSON for `Express`, sends call, parses string `z`.

Private method:
- `call(const std::string& req) -> std::string`
  - Sends request line and reads response until newline.

### 4.4 Client Entry Point

- `main(int argc, char* argv[])`
  - Parses arguments:
    - `--socket`
    - `--method`
    - `--x`, `--y`
    - `--iterations`
  - Connects to server once.
  - Loops selected method for benchmark count.
  - Prints summary:
    - `result=<...> elapsed_ms=<...> iterations=<...>`

---

## 5) Class/Method Relationship Map

### Server-side flow

1. `main` creates one shared `Calculator`.
2. Each accepted socket gets `serve_connection` in a detached thread.
3. `serve_connection` frames input message and calls `handle_request`.
4. `handle_request` maps method name to `Calculator` async method.
5. `Task<T>::get()` resolves value/exception and builds final response JSON.
6. `serve_connection` writes response with original delimiter.

### Client-side flow

1. `main` creates `Client` and calls `connect_socket`.
2. Method loop calls one of `multiply_async` / `divide_async` / `express_async`.
3. Each wrapper invokes private `call`.
4. Parsed value is returned through `Task<T>::get()`.
5. `main` prints final elapsed benchmark line.

---

## 6) Notes

- The code uses a coroutine-style API (`Task<T>`) while preserving simple socket I/O.
- JSON handling is intentionally lightweight via manual field extraction.
- Request framing supports both newline and NUL delimiters for compatibility.
- The external API behavior matches other runtimes in this repository.
