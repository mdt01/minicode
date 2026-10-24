"""minicode v0.6: connect to MCP servers and offer their tools to the model.

Servers are listed in the workspace's .mcp.json, in the format most agents use:

    {"mcpServers": {
        "campushub": {"command": "python", "args": ["mcp_server/server.py"]},
        "remote-docs": {"url": "http://127.0.0.1:8765/mcp"}}}

Each server's tools appear to the model as mcp__<server>__<tool>.
"""
import asyncio
import concurrent.futures
import json
import os
import threading
from pathlib import Path
from typing import Callable

from mcp import Client, StdioServerParameters


class MCPConnection:
    """One live session with one MCP server, run on a background event loop.

    minicode's loop is synchronous; the MCP SDK is asynchronous. A private thread
    owns the session, and call() hands work to it and waits for the answer.
    """

    def __init__(self, name: str, target, timeout: float = 60):
        self.name, self.timeout = name, timeout
        self.tools: list = []
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.queue: asyncio.Queue = self._run(self._make_queue())
        ready = concurrent.futures.Future()
        asyncio.run_coroutine_threadsafe(self._session(target, ready), self.loop)
        ready.result(timeout)                       # raises if the server failed to start

    def _run(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(self.timeout)

    async def _make_queue(self) -> asyncio.Queue:
        return asyncio.Queue()

    async def _session(self, target, ready: concurrent.futures.Future) -> None:
        try:
            async with Client(target) as client:
                self.tools = (await client.list_tools()).tools      # discovery
                ready.set_result(True)
                while (job := await self.queue.get()) is not None:
                    tool, arguments, answer = job
                    try:
                        answer.set_result(await client.call_tool(tool, arguments))
                    except Exception as exc:                  # protocol or transport error
                        answer.set_exception(exc)
        except Exception as exc:
            if not ready.done():
                ready.set_exception(exc)

    def call(self, tool: str, arguments: dict) -> str:
        """Call a tool; return its text, marking failures for the model."""
        answer = concurrent.futures.Future()
        self.loop.call_soon_threadsafe(self.queue.put_nowait, (tool, arguments, answer))
        result = answer.result(self.timeout)
        text = "\n".join(c.text for c in result.content if getattr(c, "text", None))
        return f"Error: {text}" if result.is_error else (text or "(no output)")

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.queue.put_nowait, None)

    def agent_tools(self) -> dict[str, tuple[Callable, dict]]:
        """This server's tools as (function, schema) pairs, named mcp__<server>__<tool>."""
        tools = {}
        for tool in self.tools:
            name = f"mcp__{self.name}__{tool.name}"
            schema = {"type": "function", "function": {
                "name": name, "description": tool.description or "",
                "parameters": tool.input_schema}}
            tools[name] = (lambda _t=tool.name, **args: self.call(_t, args), schema)
        return tools


def target_from_config(entry: dict, root: Path):
    """Turn one .mcp.json entry into something mcp.Client can connect to."""
    if "url" in entry:
        return entry["url"]                                    # remote: streamable HTTP
    return StdioServerParameters(                              # local: a subprocess
        command=entry["command"], args=entry.get("args", []),
        env={**os.environ, **entry.get("env", {})}, cwd=entry.get("cwd", str(root)))


def connect_all(root: Path, on_event=print) -> list[MCPConnection]:
    config = root / ".mcp.json"
    if not config.is_file():
        return []
    connections = []
    for name, entry in json.loads(config.read_text(encoding="utf-8"))["mcpServers"].items():
        try:
            connection = MCPConnection(name, target_from_config(entry, root))
            connections.append(connection)
            on_event(f"  MCP: connected to {name} ({len(connection.tools)} tools)")
        except Exception as exc:
            on_event(f"  MCP: could not connect to {name}: {exc}")
    return connections
