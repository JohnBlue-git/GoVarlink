#!/usr/bin/env python3
import argparse
import asyncio
import json
import time

SERVICE_NAME = "xyz.openbmc_project.Calculator"


class VarlinkClient:
    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        self.reader, self.writer = await asyncio.open_unix_connection(self.socket_path)

    async def close(self) -> None:
        if self.writer is None:
            return
        self.writer.close()
        await self.writer.wait_closed()

    async def _call(self, method: str, parameters: dict | None = None) -> dict:
        if self.reader is None or self.writer is None:
            raise RuntimeError("client not connected")

        request = {"method": f"{SERVICE_NAME}.{method}", "parameters": parameters or {}}
        self.writer.write((json.dumps(request) + "\n").encode())
        await self.writer.drain()

        raw = await self.reader.readline()
        if not raw:
            raise RuntimeError("server closed connection")
        response = json.loads(raw.decode())
        if "error" in response and response["error"]:
            raise RuntimeError(response["error"])
        return response.get("parameters", {})

    async def multiply(self, x: int, y: int = 1) -> int:
        response = await self._call("Multiply", {"x": x, "y": y})
        return int(response["z"])

    async def divide(self, x: int, y: int = 1) -> int:
        response = await self._call("Divide", {"x": x, "y": y})
        return int(response["z"])

    async def express(self) -> str:
        response = await self._call("Express")
        return str(response["z"])


async def run(args: argparse.Namespace) -> None:
    client = VarlinkClient(args.socket)
    await client.connect()

    start = time.perf_counter()
    result: str | int = ""
    try:
        for _ in range(args.iterations):
            if args.method == "Multiply":
                result = await client.multiply(args.x, args.y)
            elif args.method == "Divide":
                result = await client.divide(args.x, args.y)
            elif args.method == "Express":
                result = await client.express()
            else:
                raise ValueError(f"Unsupported method: {args.method}")
    finally:
        await client.close()

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    print(f"result={result} elapsed_ms={elapsed_ms} iterations={args.iterations}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Python asyncio varlink calculator client")
    parser.add_argument("--socket", default="/tmp/calculator-python.sock")
    parser.add_argument("--method", default="Multiply", choices=["Multiply", "Divide", "Express"])
    parser.add_argument("--x", type=int, default=7)
    parser.add_argument("--y", type=int, default=3)
    parser.add_argument("--iterations", type=int, default=1)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()