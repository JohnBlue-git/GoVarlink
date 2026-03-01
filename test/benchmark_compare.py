#!/usr/bin/env python3
import os
import signal
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
TMP = Path("/tmp")
DEFAULT_ITERATIONS = 20000


@dataclass(frozen=True)
class RuntimeCase:
    name: str
    socket_path: Path
    server_cmd: list[str]
    client_cmd_base: list[str]


@dataclass(frozen=True)
class BenchmarkRow:
    runtime: str
    ops_per_sec: float
    avg_ms_per_op: float
    peak_rss_kib: int
    peak_hwm_kib: int


def read_status_kib(pid: int, field: str) -> int:
    status = Path(f"/proc/{pid}/status")
    if not status.exists():
        return 0
    for line in status.read_text().splitlines():
        if line.startswith(f"{field}:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    return 0


def read_rss_kib(pid: int) -> int:
    return read_status_kib(pid, "VmRSS")


def read_hwm_kib(pid: int) -> int:
    return read_status_kib(pid, "VmHWM")


def wait_for_socket(path: Path, timeout_sec: float = 5.0) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise TimeoutError(f"socket not ready: {path}")


def terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass
    proc.kill()
    proc.wait(timeout=3)


def run_cmd(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def command_works(cmd: list[str]) -> bool:
    try:
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return proc.returncode == 0
    except FileNotFoundError:
        return False


def rust_cargo_cmd() -> list[str] | None:
    cargo_on_path = shutil.which("cargo")
    if cargo_on_path and command_works([cargo_on_path, "--version"]):
        return [cargo_on_path]

    rustup_bin = Path.home() / ".cargo/bin/rustup"
    if rustup_bin.exists() and command_works([str(rustup_bin), "run", "stable", "cargo", "--version"]):
        return [str(rustup_bin), "run", "stable", "cargo"]

    return None


def prepare_go_binaries() -> None:
    go_build_dir = ROOT / "go/build"
    go_build_dir.mkdir(parents=True, exist_ok=True)
    run_cmd(["go", "build", "-o", str(go_build_dir / "go-server"), "./server"], cwd=ROOT / "go")
    run_cmd(["go", "build", "-o", str(go_build_dir / "go-client"), "./client"], cwd=ROOT / "go")


def prepare_cpp_binaries() -> None:
    run_cmd(["make", "-C", str(ROOT / "cpp")])


def prepare_rust_binaries() -> None:
    cargo_cmd = rust_cargo_cmd()
    if cargo_cmd is None:
        raise RuntimeError("Rust cargo command not available")
    run_cmd([*cargo_cmd, "build", "--release"], cwd=ROOT / "rust")


def resolve_iterations() -> int:
    raw = os.getenv("BENCHMARK_ITERATIONS", str(DEFAULT_ITERATIONS))
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_ITERATIONS
    return max(value, 1)


def cleanup_enabled() -> bool:
    return os.getenv("BENCHMARK_CLEANUP", "0") == "1"


def cleanup_build_artifacts(*, clean_go: bool, clean_cpp: bool, clean_rust: bool) -> None:
    if clean_go:
        for path in [ROOT / "go/build/go-server", ROOT / "go/build/go-client"]:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass

        go_build_dir = ROOT / "go/build"
        try:
            if go_build_dir.exists() and not any(go_build_dir.iterdir()):
                go_build_dir.rmdir()
        except OSError:
            pass

    if clean_cpp:
        cpp_build = ROOT / "cpp/build"
        if cpp_build.exists():
            try:
                run_cmd(["make", "-C", str(ROOT / "cpp"), "clean"])
            except subprocess.CalledProcessError:
                pass

    if clean_rust:
        rust_target = ROOT / "rust/target"
        if rust_target.exists():
            cargo_cmd = rust_cargo_cmd()
            if cargo_cmd is not None:
                try:
                    run_cmd([*cargo_cmd, "clean"], cwd=ROOT / "rust")
                except subprocess.CalledProcessError:
                    pass


def render_grid(rows: list[BenchmarkRow]) -> str:
    headers = ["runtime", "Ops/s", "Avg ms/op", "Peak RSS (KiB)", "Peak HWM (KiB)"]
    body = [
        [
            row.runtime,
            f"{row.ops_per_sec:.2f}",
            f"{row.avg_ms_per_op:.4f}",
            str(row.peak_rss_kib),
            str(row.peak_hwm_kib),
        ]
        for row in rows
    ]

    table = [headers, *body]
    widths = [max(len(r[i]) for r in table) for i in range(len(headers))]

    def sep() -> str:
        return "+-" + "-+-".join("-" * w for w in widths) + "-+"

    def fmt(cells: list[str]) -> str:
        return "| " + " | ".join(cells[i].ljust(widths[i]) for i in range(len(cells))) + " |"

    lines = [sep(), fmt(headers), sep()]
    for row in body:
        lines.append(fmt(row))
    lines.append(sep())
    return "\n".join(lines)


@pytest.fixture(scope="session", autouse=True)
def ensure_tooling() -> None:
    if not command_exists("python3"):
        pytest.skip("Missing required tool: python3")

    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_generated_artifacts(runtime_availability: dict[str, dict[str, Any]]) -> None:
    yield

    if not cleanup_enabled():
        return

    cleanup_build_artifacts(
        clean_go=True,
        clean_cpp=True,
        clean_rust=True,
    )


@pytest.fixture(scope="session")
def runtime_availability() -> dict[str, dict[str, Any]]:
    (ROOT / "go/build").mkdir(parents=True, exist_ok=True)

    availability: dict[str, dict[str, Any]] = {
        "python": {"available": True, "reason": ""},
        "go": {"available": False, "reason": ""},
        "cpp": {"available": False, "reason": ""},
        "rust": {"available": False, "reason": ""},
    }

    go_server = ROOT / "go/build/go-server"
    go_client = ROOT / "go/build/go-client"
    if go_server.exists() and go_client.exists():
        availability["go"] = {"available": True, "reason": ""}
    elif command_exists("go"):
        try:
            prepare_go_binaries()
            availability["go"] = {"available": True, "reason": ""}
        except subprocess.CalledProcessError as exc:
            availability["go"] = {
                "available": False,
                "reason": f"go build failed: {exc}",
            }
    else:
        availability["go"] = {
            "available": False,
            "reason": "go not found in PATH",
        }

    cpp_server = ROOT / "cpp/build/calculator_server"
    cpp_client = ROOT / "cpp/build/calculator_client"
    if cpp_server.exists() and cpp_client.exists():
        availability["cpp"] = {"available": True, "reason": ""}
    elif command_exists("make") and command_exists("g++"):
        try:
            prepare_cpp_binaries()
            availability["cpp"] = {"available": True, "reason": ""}
        except subprocess.CalledProcessError as exc:
            availability["cpp"] = {
                "available": False,
                "reason": f"cpp build failed: {exc}",
            }
    else:
        availability["cpp"] = {
            "available": False,
            "reason": "make and/or g++ not found in PATH",
        }

    rust_server = ROOT / "rust/target/release/calculator_server"
    rust_client = ROOT / "rust/target/release/calculator_client"
    cargo_cmd = rust_cargo_cmd()
    if rust_server.exists() and rust_client.exists() and cargo_cmd is not None:
        availability["rust"] = {"available": True, "reason": ""}
    elif cargo_cmd is not None:
        try:
            prepare_rust_binaries()
            availability["rust"] = {"available": True, "reason": ""}
        except (subprocess.CalledProcessError, RuntimeError) as exc:
            availability["rust"] = {
                "available": False,
                "reason": f"rust build failed: {exc}",
            }
    else:
        availability["rust"] = {
            "available": False,
            "reason": "working cargo command not found (PATH cargo or rustup stable)",
        }

    return availability


@pytest.fixture(scope="session")
def benchmark_results(request: pytest.FixtureRequest) -> list[BenchmarkRow]:
    rows: list[BenchmarkRow] = []
    yield rows

    if not rows:
        return

    runtime_order = {"go": 0, "python": 1, "cpp": 2, "rust": 3}
    ordered = sorted(rows, key=lambda row: runtime_order.get(row.runtime, 99))
    grid = render_grid(ordered)

    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line("")
        reporter.write_line("Benchmark Summary (grid style)")
        for line in grid.splitlines():
            reporter.write_line(line)


@pytest.fixture(scope="session")
def runtime_cases() -> list[RuntimeCase]:
    go_socket = TMP / "calculator-go.sock"
    py_socket = TMP / "calculator-python.sock"
    cpp_socket = TMP / "calculator-cpp.sock"
    rust_socket = TMP / "calculator-rust.sock"

    return [
        RuntimeCase(
            name="go",
            socket_path=go_socket,
            server_cmd=[str(ROOT / "go/build/go-server"), "--socket", str(go_socket)],
            client_cmd_base=[
                str(ROOT / "go/build/go-client"),
                "--socket",
                str(go_socket),
                "--method",
                "Multiply",
                "--x",
                "7",
                "--y",
                "3",
            ],
        ),
        RuntimeCase(
            name="python",
            socket_path=py_socket,
            server_cmd=["python3", str(ROOT / "python/server.py"), "--socket", str(py_socket)],
            client_cmd_base=[
                "python3",
                str(ROOT / "python/client.py"),
                "--socket",
                str(py_socket),
                "--method",
                "Multiply",
                "--x",
                "7",
                "--y",
                "3",
            ],
        ),
        RuntimeCase(
            name="cpp",
            socket_path=cpp_socket,
            server_cmd=[str(ROOT / "cpp/build/calculator_server"), "--socket", str(cpp_socket)],
            client_cmd_base=[
                str(ROOT / "cpp/build/calculator_client"),
                "--socket",
                str(cpp_socket),
                "--method",
                "Multiply",
                "--x",
                "7",
                "--y",
                "3",
            ],
        ),
        RuntimeCase(
            name="rust",
            socket_path=rust_socket,
            server_cmd=[str(ROOT / "rust/target/release/calculator_server"), "--socket", str(rust_socket)],
            client_cmd_base=[
                str(ROOT / "rust/target/release/calculator_client"),
                "--socket",
                str(rust_socket),
                "--method",
                "Multiply",
                "--x",
                "7",
                "--y",
                "3",
            ],
        ),
    ]


@pytest.fixture(params=["go", "python", "cpp", "rust"])
def runtime_case(
    request: pytest.FixtureRequest,
    runtime_cases: list[RuntimeCase],
    runtime_availability: dict[str, dict[str, Any]],
) -> RuntimeCase:
    name = request.param
    status = runtime_availability.get(name, {"available": False, "reason": "unknown runtime"})
    if not status["available"]:
        if name == "rust":
            pytest.fail(f"rust unavailable: {status['reason']}")
        pytest.skip(f"{name} unavailable: {status['reason']}")

    for case in runtime_cases:
        if case.name == name:
            return case
    raise RuntimeError(f"Unknown runtime case: {name}")


@pytest.fixture
def server_process(runtime_case: RuntimeCase) -> subprocess.Popen:
    if runtime_case.socket_path.exists():
        runtime_case.socket_path.unlink()

    proc = subprocess.Popen(runtime_case.server_cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_for_socket(runtime_case.socket_path)
    try:
        yield proc
    finally:
        terminate_process(proc)
        if runtime_case.socket_path.exists():
            runtime_case.socket_path.unlink()


def test_benchmark_runtime(
    runtime_case: RuntimeCase,
    server_process: subprocess.Popen,
    benchmark_results: list[BenchmarkRow],
) -> None:
    iterations = resolve_iterations()
    client_cmd = [*runtime_case.client_cmd_base, "--iterations", str(iterations)]

    rss_before = read_rss_kib(server_process.pid)
    hwm_before = read_hwm_kib(server_process.pid)
    start = time.perf_counter()
    subprocess.run(client_cmd, cwd=ROOT, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elapsed = time.perf_counter() - start
    rss_after = read_rss_kib(server_process.pid)
    hwm_after = read_hwm_kib(server_process.pid)
    peak_rss_kib = max(rss_before, rss_after)
    peak_hwm_kib = max(hwm_before, hwm_after)
    ops_per_sec = iterations / elapsed if elapsed > 0 else 0.0
    avg_ms_per_op = (elapsed * 1000.0) / iterations if iterations > 0 else 0.0

    benchmark_results.append(
        BenchmarkRow(
            runtime=runtime_case.name,
            ops_per_sec=ops_per_sec,
            avg_ms_per_op=avg_ms_per_op,
            peak_rss_kib=peak_rss_kib,
            peak_hwm_kib=peak_hwm_kib,
        )
    )
    assert elapsed > 0
    assert peak_rss_kib >= 0
    assert peak_hwm_kib >= 0