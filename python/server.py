#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
from dataclasses import dataclass

SERVICE_NAME = "xyz.openbmc_project.Calculator"
OBJECT_PATH = "/xyz/openbmc_project/calculator"
VARLINK_SERVICE = "org.varlink.service"

SERVICE_INTERFACE_DESCRIPTION = """interface xyz.openbmc_project.Calculator

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
"""


@dataclass
class CalculatorState:
    last_result: int = 0
    status: str = "Success"
    base: str = "Decimal"
    owner: str = "root"


class Calculator:
    def __init__(self) -> None:
        self._state = CalculatorState()
        self._lock = asyncio.Lock()

    async def multiply(self, x: int, y: int = 1) -> int:
        async with self._lock:
            z = x * y
            self._state.last_result = z
            self._state.status = "Success"
            return z

    async def divide(self, x: int, y: int = 1) -> int:
        async with self._lock:
            if y == 0:
                self._state.status = "Error"
                raise ValueError("DivisionByZero")
            z = x // y
            self._state.last_result = z
            self._state.status = "Success"
            return z

    async def express(self) -> str:
        async with self._lock:
            value = self._state.last_result
            base = self._state.base
        if base == "Binary":
            return format(value, "b")
        if base == "Heximal":
            return format(value, "x")
        return str(value)

    async def clear(self) -> None:
        async with self._lock:
            self._state.last_result = 0
            self._state.status = "Success"

    async def get_state(self) -> dict:
        async with self._lock:
            return {
                "lastResult": self._state.last_result,
                "status": self._state.status,
                "base": self._state.base,
                "owner": self._state.owner,
                "objectPath": OBJECT_PATH,
                "serviceName": SERVICE_NAME,
            }

    async def set_owner(self, owner: str) -> None:
        if os.getenv("CALCULATOR_ALLOW_OWNER_CHANGE") != "1":
            async with self._lock:
                self._state.status = "Error"
            raise PermissionError("PermissionDenied")

        async with self._lock:
            self._state.owner = owner
            self._state.status = "Success"


async def dispatch(calc: Calculator, request: dict) -> dict:
    method = request.get("method", "")
    params = request.get("parameters", {}) or {}
    try:
        if method == f"{VARLINK_SERVICE}.GetInfo":
            return {
                "parameters": {
                    "vendor": "GoVarlink",
                    "product": SERVICE_NAME,
                    "version": "0.1.0",
                    "url": "https://github.com/JohnBlue-git/GoVarlink",
                    "interfaces": [SERVICE_NAME],
                }
            }
        if method == f"{VARLINK_SERVICE}.GetInterfaceDescription":
            iface = str(params["interface"])
            if iface != SERVICE_NAME:
                return {"error": "org.varlink.service.InterfaceNotFound"}
            return {"parameters": {"description": SERVICE_INTERFACE_DESCRIPTION}}
        if method == f"{SERVICE_NAME}.Multiply":
            z = await calc.multiply(int(params["x"]), int(params.get("y", 1)))
            return {"parameters": {"z": z}}
        if method == f"{SERVICE_NAME}.Divide":
            z = await calc.divide(int(params["x"]), int(params.get("y", 1)))
            return {"parameters": {"z": z}}
        if method == f"{SERVICE_NAME}.Express":
            z = await calc.express()
            return {"parameters": {"z": z}}
        if method == f"{SERVICE_NAME}.Clear":
            await calc.clear()
            return {"parameters": {}}
        if method == f"{SERVICE_NAME}.GetState":
            state = await calc.get_state()
            return {"parameters": {"state": state}}
        if method == f"{SERVICE_NAME}.SetOwner":
            await calc.set_owner(str(params["owner"]))
            return {"parameters": {}}
        return {"error": "org.varlink.service.MethodNotImplemented"}
    except KeyError:
        return {"error": "org.varlink.service.InvalidParameter"}
    except ValueError as exc:
        if str(exc) == "DivisionByZero":
            return {"error": f"{SERVICE_NAME}.DivisionByZero"}
        return {"error": "org.varlink.service.InvalidParameter"}
    except PermissionError as exc:
        if str(exc) == "PermissionDenied":
            return {"error": f"{SERVICE_NAME}.PermissionDenied"}
        return {"error": "org.varlink.service.InternalError"}
    except Exception:
        return {"error": "org.varlink.service.InternalError"}


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, calc: Calculator) -> None:
    async def read_message() -> tuple[bytes, bytes] | tuple[None, None]:
        buffer = bytearray()
        while True:
            chunk = await reader.read(1)
            if not chunk:
                if buffer:
                    return bytes(buffer), b"\n"
                return None, None
            if chunk in (b"\n", b"\x00"):
                return bytes(buffer), chunk
            buffer.extend(chunk)

    try:
        while True:
            raw, delimiter = await read_message()
            if raw is None:
                break
            try:
                request = json.loads(raw.decode())
            except json.JSONDecodeError:
                response = {"error": "org.varlink.service.InvalidParameter"}
                writer.write(json.dumps(response).encode() + delimiter)
                await writer.drain()
                continue

            response = await dispatch(calc, request)
            if not request.get("oneway", False):
                writer.write(json.dumps(response).encode() + delimiter)
                await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Python asyncio varlink calculator server")
    parser.add_argument("--socket", default="/tmp/calculator-python.sock")
    args = parser.parse_args()

    try:
        os.unlink(args.socket)
    except FileNotFoundError:
        pass

    calc = Calculator()
    server = await asyncio.start_unix_server(
        lambda r, w: handle_client(r, w, calc),
        path=args.socket,
    )

    print(f"Python calculator server on {args.socket}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())