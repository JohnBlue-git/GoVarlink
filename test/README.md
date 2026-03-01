# Test Benchmark Structure Guide

This document explains the internal structure of `benchmark_compare.py` in the `test/` folder.

## 1) Purpose

`benchmark_compare.py` is a pytest-based benchmark that compares four runtime implementations of the same Varlink calculator service:

- `go`
- `python`
- `cpp`
- `rust`

For each runtime, it starts the server, runs the client benchmark, and records:

- `Ops/s`
- `Avg ms/op`
- `Peak RSS (KiB)`
- `Peak HWM (KiB)`

It then prints a grid-style summary at the end of the test session.

---

## 2) File Structure (High-Level)

The file is organized into 5 logical blocks:

1. **Data models**
   - `RuntimeCase`: runtime name, socket path, server command, client command base
   - `BenchmarkRow`: benchmark result row used for final report

2. **Utility helpers**
   - process/memory helpers (`read_status_kib`, `read_rss_kib`, `read_hwm_kib`, `terminate_process`)
   - command/build helpers (`run_cmd`, `command_exists`, `command_works`, `rust_cargo_cmd`)
   - runtime build helpers (`prepare_go_binaries`, `prepare_cpp_binaries`, `prepare_rust_binaries`)
   - benchmark settings/cleanup (`resolve_iterations`, `cleanup_enabled`, `cleanup_build_artifacts`)
   - output formatter (`render_grid`)

3. **Session fixtures**
   - `ensure_tooling`
   - `runtime_availability`
   - `benchmark_results`
   - `runtime_cases`

4. **Per-case fixtures**
   - `runtime_case` (parametrized by runtime)
   - `server_process` (start/stop server per test case)

5. **Benchmark test body**
   - `test_benchmark_runtime`

---

## 2.1) Class Information and Method Relationship

Although this file does not define traditional OOP service classes, it has two important dataclass-based models that act like core data classes:

### `RuntimeCase` (dataclass)

Fields:

- `name`: runtime id (`go`, `python`, `cpp`, `rust`)
- `socket_path`: UNIX socket path for server/client communication
- `server_cmd`: full command used to start runtime server
- `client_cmd_base`: base command used by benchmark client invocation

Methods/functions directly related to `RuntimeCase`:

- `runtime_cases()` **creates** all `RuntimeCase` instances.
- `runtime_case(...)` **selects** one `RuntimeCase` by pytest parameter.
- `server_process(runtime_case)` **consumes** selected `RuntimeCase` to launch/teardown server.
- `test_benchmark_runtime(runtime_case, ...)` **consumes** selected `RuntimeCase` to build benchmark client command.

### `BenchmarkRow` (dataclass)

Fields:

- `runtime`: runtime name
- `ops_per_sec`: throughput
- `avg_ms_per_op`: latency
- `peak_rss_kib`: peak RSS from server process
- `peak_hwm_kib`: peak HWM from server process

Methods/functions directly related to `BenchmarkRow`:

- `test_benchmark_runtime(...)` **creates** `BenchmarkRow` and appends it into `benchmark_results`.
- `benchmark_results(...)` **collects/sorts** `BenchmarkRow` rows and triggers final rendering.
- `render_grid(rows)` **formats** `BenchmarkRow` rows as the final ASCII grid table.

Relationship summary:

- `RuntimeCase` controls **how to run** each runtime.
- `BenchmarkRow` records **what was measured** for each runtime.
- The benchmark pipeline is: `RuntimeCase` selection → execution → `BenchmarkRow` result aggregation.

---

## 3) Execution Flow

During `pytest -q -s test/benchmark_compare.py`, execution flow is:

1. `ensure_tooling` checks baseline requirement (`python3`).
2. `runtime_availability` checks/builds Go/C++/Rust binaries.
3. `cleanup_generated_artifacts` registers session-end cleanup behavior.
4. `runtime_case` iterates runtime params: `go`, `python`, `cpp`, `rust`.
5. `server_process` starts the selected runtime server and waits for socket readiness.
6. `test_benchmark_runtime` runs client benchmark with `--iterations`.
7. Test collects elapsed time and memory fields from `/proc/<pid>/status`.
8. A `BenchmarkRow` is appended to session `benchmark_results`.
9. At session end, `benchmark_results` prints one grid table.
10. Session cleanup runs in `cleanup_generated_artifacts`.

---

## 4) Fixture Design

### `ensure_tooling` (session, autouse)

- Verifies `python3` exists.

### `cleanup_generated_artifacts` (session, autouse)

- Runs session-end cleanup decision.
- Performs artifact cleanup only when `BENCHMARK_CLEANUP=1`.

### `runtime_availability` (session)

- Prepares runtime availability map.
- Build behavior:
  - Go: uses `go build` into `go/build/`
  - C++: uses `make -C cpp`
  - Rust: uses `cargo build --release` (or rustup stable cargo)
- Stores `{available: bool, reason: str}` per runtime.

### `benchmark_results` (session)

- Keeps all runtime rows.
- Teardown hook sorts rows and prints grid output in fixed order (`go`, `python`, `cpp`, `rust`).

### `runtime_cases` (session)

- Defines static runtime command matrix:
  - socket path
  - server command
  - client command base

### `runtime_case` (function, parametrized)

- Iterates target runtimes with `params=["go", "python", "cpp", "rust"]`.
- Runtime behavior when unavailable:
  - `rust`: **fail test** (mandatory)
  - others: `pytest.skip`

### `server_process` (function)

- Removes stale socket file.
- Starts server process.
- Waits for socket readiness.
- Ensures process termination + socket cleanup in `finally`.

---

## 5) Benchmark Calculation Logic

Inside `test_benchmark_runtime`:

- Iteration count from `BENCHMARK_ITERATIONS` (default `20000`, minimum `1`).
- Measures elapsed wall clock around one client benchmark run.
- Reads memory before/after from server PID:
  - RSS (`VmRSS`)
  - HWM (`VmHWM`)
- Computes:
  - `Ops/s = iterations / elapsed`
  - `Avg ms/op = (elapsed * 1000) / iterations`
- Stores one result row per runtime.

---

## 6) Environment Variables

- `BENCHMARK_ITERATIONS`
  - Controls per-runtime benchmark iterations.
  - Invalid values fall back to default `20000`.

- `BENCHMARK_CLEANUP`
  - When set to `1`, cleanup runs after session:
    - removes `go/build/go-server`, `go/build/go-client`
    - removes `go/build/` if empty
    - runs `make clean` under `cpp/`
    - runs `cargo clean` under `rust/` (when cargo is available)
