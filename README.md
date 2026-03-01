## GoVarlink - xyz.openbmc_project.Calculator

This repository generates a Varlink-based calculator service from the OpenBMC-style interface definition:

- Service: `xyz.openbmc_project.Calculator`
- Object path: `/xyz/openbmc_project/calculator`
- Source interface files:
	- `xyz/openbmc_project/Calculator.interface.yaml`
	- `xyz/openbmc_project/Calculator.events.yaml`

Implemented runtimes:

- C++20 (coroutine style)
- Go (goroutine/channel async style)
- Python (`asyncio` async/await)
- Rust (`tokio` async/await)

> Note: Varlink has no native D-Bus signal equivalent.
> `Cleared` event from `Calculator.events.yaml` is intentionally skipped in transport-level implementation.
> A future Redis pub/sub bridge can be added for event broadcasting.

---

## 1) Interface Mapping

Varlink IDL is generated at:

- `varlink/xyz.openbmc_project.Calculator.varlink`

Mapped methods:

- `Multiply(x, y?) -> z`
- `Divide(x, y?) -> z` (`DivisionByZero` on divide by zero)
- `Express() -> z`
- `Clear() -> ()`
- `GetState() -> state` (captures `LastResult`, `Status`, `Base`, `Owner`, `objectPath`, `serviceName`)
- `SetOwner(owner) -> ()` (`PermissionDenied` unless env `CALCULATOR_ALLOW_OWNER_CHANGE=1`)

OpenBMC property handling:

- `LastResult`, `Status`, `Base`, `Owner` are modeled inside `GetState` and `SetOwner`.
- `Base` is kept as `Decimal` by default in this MVP implementation.

---

## 2) Folder Layout

```text
.
├── cpp/
│   ├── Makefile
│   ├── server.cpp
│   └── client.cpp
├── go/
│   ├── go.mod
│   ├── server/main.go
│   └── client/main.go
├── python/
│   ├── server.py
│   └── client.py
├── rust/
│   ├── Cargo.toml
│   └── src/bin/
│       ├── server.rs
│       └── client.rs
├── test/
│   └── benchmark_compare.py
├── varlink/
│   └── xyz.openbmc_project.Calculator.varlink
└── xyz/
		└── openbmc_project/
				├── Calculator.interface.yaml
				└── Calculator.events.yaml
```

---

## 3) Pre-install

Ubuntu (including dev container) pre-install:

```bash
sudo apt update
sudo apt install -y make g++ golang-go python3 python3-pip curl
python3 -m pip install -U pytest
```

Rust build requirement:

- To build `rust/` binaries, a working Rust toolchain (`cargo` + `rustc`) is required.

Option A (have sudo/root): install Rust from apt

```bash
sudo apt install -y rustc cargo
cargo --version
rustc --version
```

Option B (no sudo/root): install Rust toolchain in user space via rustup

```bash
curl https://sh.rustup.rs -sSf | sh -s -- -y
source "$HOME/.cargo/env"
cargo --version
rustc --version
```

If you do not want to modify shell profile, you can call cargo through rustup directly:

```bash
$HOME/.cargo/bin/rustup run stable cargo --version
$HOME/.cargo/bin/rustup run stable rustc --version
```

---

## 4) Run Servers and Clients

### 4.1 Python (async/await)

Start server:

```bash
python3 python/server.py --socket /tmp/calculator-python.sock
```

Call method:

```bash
python3 python/client.py --socket /tmp/calculator-python.sock --method Multiply --x 7 --y 3
```

### 4.2 Go (async style by goroutines/channels)

Build:

```bash
cd go
mkdir -p build
go build -o ./build/go-server ./server
go build -o ./build/go-client ./client
cd ..
```

Start server:

```bash
./go/build/go-server --socket /tmp/calculator-go.sock
```

Call method:

```bash
./go/build/go-client --socket /tmp/calculator-go.sock --method Multiply --x 7 --y 3
```

### 4.3 C++20 (coroutine style)

Build:

```bash
make -C cpp
```

Start server:

```bash
./cpp/build/calculator_server --socket /tmp/calculator-cpp.sock
```

Call method:

```bash
./cpp/build/calculator_client --socket /tmp/calculator-cpp.sock --method Multiply --x 7 --y 3
```

### 4.4 Rust (tokio async/await)

Build:

```bash
cd rust
cargo build --release
cd ..
```

If `cargo` is not in PATH (rustup user install), use:

```bash
cd rust
$HOME/.cargo/bin/rustup run stable cargo build --release
cd ..
```

Start server:

```bash
./rust/target/release/calculator_server --socket /tmp/calculator-rust.sock
```

Call method:

```bash
./rust/target/release/calculator_client --socket /tmp/calculator-rust.sock --method Multiply --x 7 --y 3
```

---

## 5) Varlink Interface and Control

### 5.1) varlinkctl

`varlinkctl` is a CLI tool to inspect and call Varlink services.

Compatibility note for this repository:

- All four servers (Go/Python/C++/Rust) support `varlinkctl info`, `list-interfaces`, and `call` in this project.
- `introspect` works and returns interface description, but `varlinkctl` may show a parser warning for this interface naming style while still printing raw description.
- Project-specific clients are still useful for benchmark automation and API regression checks.

Basic commands:

```bash
varlinkctl --no-pager info <address>
varlinkctl --no-pager list-interfaces <address>
varlinkctl --no-pager introspect <address> <interface>
varlinkctl --no-pager call <address> <method> '<json-params>'
```

What they do:

- `info`: show service-level metadata.
- `list-interfaces`: show interfaces implemented by the service.
- `introspect`: show interface definition/IDL for one interface.
- `call`: invoke one method with JSON parameters.

Generic call format:

```bash
varlinkctl --no-pager call <address> <method> '<json-params>'
```

### 5.2) commands for this project

For this project, use UNIX socket addresses:

- `unix:/tmp/calculator-go.sock`
- `unix:/tmp/calculator-python.sock`
- `unix:/tmp/calculator-cpp.sock`
- `unix:/tmp/calculator-rust.sock`

Method naming format:

- `xyz.openbmc_project.Calculator.Multiply`
- `xyz.openbmc_project.Calculator.Divide`
- `xyz.openbmc_project.Calculator.Express`
- `xyz.openbmc_project.Calculator.Clear`
- `xyz.openbmc_project.Calculator.GetState`
- `xyz.openbmc_project.Calculator.SetOwner`

Example with Go server:

```bash
./go/build/go-server --socket /tmp/calculator-go.sock
```

In another terminal, inspect service with `varlinkctl`:

```bash
varlinkctl --no-pager info unix:/tmp/calculator-go.sock
varlinkctl --no-pager list-interfaces unix:/tmp/calculator-go.sock
varlinkctl --no-pager introspect unix:/tmp/calculator-go.sock xyz.openbmc_project.Calculator
```

Call methods with `varlinkctl`:

```bash
varlinkctl --no-pager call unix:/tmp/calculator-go.sock xyz.openbmc_project.Calculator.Multiply '{"x":7,"y":3}'
varlinkctl --no-pager call unix:/tmp/calculator-go.sock xyz.openbmc_project.Calculator.Divide '{"x":21,"y":3}'
varlinkctl --no-pager call unix:/tmp/calculator-go.sock xyz.openbmc_project.Calculator.Express '{}'
varlinkctl --no-pager call unix:/tmp/calculator-go.sock xyz.openbmc_project.Calculator.Clear '{}'
varlinkctl --no-pager call unix:/tmp/calculator-go.sock xyz.openbmc_project.Calculator.GetState '{}'
```

Set owner example (requires permission env in server process):

```bash
CALCULATOR_ALLOW_OWNER_CHANGE=1 ./go/build/go-server --socket /tmp/calculator-go.sock
varlinkctl --no-pager call unix:/tmp/calculator-go.sock xyz.openbmc_project.Calculator.SetOwner '{"owner":"admin"}'
```

You can replace Go socket/binary with Python/C++/Rust server sockets shown above.

---

## 6) Benchmark / Test Usage (pytest + fixtures)

Benchmark test file:

- `test/benchmark_compare.py`

This file is now a **pytest-style benchmark test** and uses fixtures to:

- verify baseline tooling (`ensure_tooling`)
- build Go/C++/Rust binaries (`runtime_availability`)
- prepare runtime matrix (`runtime_cases`)
- parameterize test targets (`runtime_case`: go/python/cpp/rust)
- start/stop each runtime server per case (`server_process`)

Rust runtime is mandatory in this benchmark suite; if Rust toolchain is unavailable, the test fails instead of skipping Rust.

### 6.1 Install test dependency

```bash
python3 -m pip install pytest
```

### 6.2 Run benchmark tests

Run all runtimes with default iterations (`20000`):

```bash
pytest -q -s test/benchmark_compare.py
```

Run with custom iterations:

```bash
BENCHMARK_ITERATIONS=50000 pytest -q -s test/benchmark_compare.py
```

Run with cleanup after test (remove built Go/C++/Rust artifacts):

```bash
BENCHMARK_CLEANUP=1 pytest -q -s test/benchmark_compare.py
```

Cleanup behavior:


- If `BENCHMARK_CLEANUP=1`, full cleanup runs:
	- remove `go/build/go-*` and remove `go/build/` if empty
	- run `make clean` under `cpp/`
	- run `cargo clean` under `rust/`

Run only one runtime case (example: Go):

```bash
pytest -q -s test/benchmark_compare.py::test_benchmark_runtime[go]
```

### 6.3 Example output

Varlink (this project):

```text
Benchmark Summary
+---------+-----------+-----------+----------------+----------------+
| runtime | Ops/s     | Avg ms/op | Peak RSS (KiB) | Peak HWM (KiB) |
+---------+-----------+-----------+----------------+----------------+
| go      | 7602.93   | 0.1315    | 10500          | 10500          |
| python  | 3331.45   | 0.3002    | 22144          | 22144          |
| cpp     | 20350.90  | 0.0491    | 3840           | 3840           |
| rust    | 17688.43  | 0.0565    | 2944           | 2944           |
+---------+-----------+-----------+----------------+----------------+
```

D-Bus reference (SDbusplus C++ coroutine design):

Source: https://github.com/JohnBlue-git/HowToSDBusPlus/blob/main/my-calculator/README.md

```text
+---------+-----------+-----------+----------------+----------------+
| runtime | Ops/s     | Avg ms/op | Peak RSS (KiB) | Peak HWM (KiB) |
+---------+-----------+-----------+----------------+----------------+
| dbus-cpp| 213.35    | 4.6872    | 4992           | 4992           |
+---------+-----------+-----------+----------------+----------------+
```

### 6.4 Result field meaning

- `runtime`: measured implementation (`go`, `python`, `cpp`, `rust`)
- `Ops/s`: operations per second (throughput). Higher is better.
- `Avg ms/op`: average milliseconds per operation (latency). Lower is better.
	Roughly inverse to `Ops/s`: `Avg ms/op ≈ 1000 / Ops/s`.
- `Peak RSS (KiB)`: peak resident memory usage (physical RAM used), in KiB. Lower is generally better.
- `Peak HWM (KiB)`: high-water mark of resident memory during process lifetime (highest RSS ever reached), in KiB.

### 6.5 Comparison: Varlink vs D-Bus

Why Varlink is often faster in this kind of benchmark:

- Leaner request/response path: Varlink is designed around a simple RPC model, so the call path is usually shorter.
- Broker vs direct socket path:
    - D-Bus typically uses a broker/daemon routing model, while this Varlink sample uses direct UNIX socket client↔server communication.
	- The broker hop can introduce extra context switches and extra data movement/copy steps in the message path.
- Lower protocol/stack overhead: D-Bus commonly involves richer semantics and extra framework layers, which can add per-call cost.
- Serialization cost profile: in this workload (small calculator RPC), lightweight JSON message handling can be cheaper than a more feature-rich bus stack.
- Service model difference: bus-oriented architectures provide routing/introspection/features that are valuable, but those features are not free in latency/throughput.

Call flow (high level):

```text
D-Bus (broker design)
Client Process
	-> D-Bus Broker/Daemon
			-> Service Process
			<- D-Bus Broker/Daemon
	<- Client Process

Varlink (direct socket design)
Client Process
	-> Service Process (UNIX socket)
	<- Service Process
```

Per-request flow detail:

- D-Bus flow:
	1. Client marshals request.
	2. Client sends request to broker.
	3. Broker routes request to destination service.
	4. Service handles request and returns reply to broker.
	5. Broker routes reply back to client.
- Varlink flow:
	1. Client serializes request.
	2. Client writes directly to service UNIX socket.
	3. Service handles request and writes reply directly back.

Important caveat:

- These numbers are workload-specific and implementation-specific.
- A fair Varlink vs D-Bus conclusion should keep hardware, compiler flags, iteration count, and request shape identical.

Based on the latest `BENCHMARK_ITERATIONS=20000` sample (same metric format):

- Throughput: Varlink C++ (`20350.90 Ops/s`) vs D-Bus C++ (`213.35 Ops/s`) is about **95.39x higher**.
- Latency: Varlink C++ (`0.0491 ms/op`) vs D-Bus C++ (`4.6872 ms/op`) is about **95.46x lower**.
- Memory (RSS): Varlink C++ (`3840 KiB`) vs D-Bus C++ (`4992 KiB`) is about **23.1% lower**.
- Memory (HWM): Varlink C++ (`3840 KiB`) vs D-Bus C++ (`4992 KiB`) is about **23.1% lower**.

Conclusion from this dataset: the Varlink implementation in this project is significantly faster and uses less resident memory than the referenced D-Bus sample.

### 6.6 Comparison: Varlink across languages

Brief interpretation for this benchmark run:

- Why Go is slower than C++/Rust here:
	- Go runtime adds scheduler and GC-related overhead, which can be visible in small, high-frequency RPC workloads.
	- This sample has many short calls and frequent JSON handling; runtime bookkeeping cost can take a larger share per operation.
- About Rust vs C++:
	- In repeated runs, Rust and C++ can trade places depending on run conditions.
	- For this project, they should be treated as the same performance tier rather than having a fixed winner.

Important note: these are workload-specific observations, not universal language rankings. Different compilers, flags, allocators, and implementation details can change the order.

Throughput ranking (`Ops/s`, higher is better):

1. `cpp` (`20350.90`)
2. `rust` (`17688.43`)
3. `go` (`7602.93`)
4. `python` (`3331.45`)

Latency ranking (`Avg ms/op`, lower is better):

1. `cpp` (`0.0491`)
2. `rust` (`0.0565`)
3. `go` (`0.1315`)
4. `python` (`0.3002`)

Memory footprint (`Peak RSS/HWM`, lower is better):

1. `rust` (`2944 / 2944 KiB`)
2. `cpp` (`3840 / 3840 KiB`)
3. `go` (`10500 / 10500 KiB`)
4. `python` (`22144 / 22144 KiB`)

Relative view from this sample:

- `cpp` and `rust` are very close in performance and can swap rank across repeated runs.
- `cpp`/`rust` are both clearly faster than `go` and `python` in this workload.
- `cpp` throughput is about **2.68x** of `go`, and **6.11x** of `python`.
- `rust` throughput is about **2.33x** of `go`, and **5.31x** of `python`.
- `go` throughput is about **2.28x** of `python`.
- `go` remains balanced; `python` is convenient for rapid iteration.

---

## 7) Error Semantics

- `xyz.openbmc_project.Calculator.DivisionByZero`
- `xyz.openbmc_project.Calculator.PermissionDenied`
- plus generic:
	- `org.varlink.service.InvalidParameter`
	- `org.varlink.service.MethodNotImplemented`
	- `org.varlink.service.InternalError`

---

## 8) Future Extension

For signal/event support (e.g., `Cleared`), a practical next step is:

1. Publish event payload to Redis channel (server side).
2. Let subscribers consume Redis stream/pubsub.
3. Optionally expose a lightweight event gateway for non-Redis clients.

This keeps request/response on Varlink while adding broadcast semantics outside Varlink core.
