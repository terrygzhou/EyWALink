"""MCP (Model Context Protocol) tool integration layer.

Provides a minimal, dependency-light MCP client so agent nodes can call
external tools (file ops, shell, search, custom servers) through a uniform
interface. Zero lock-in: any MCP server speaks JSON-RPC 2.0 over stdio or
HTTP; we implement both transports directly.

Design goals:
- No mandatory SDK: pure httpx + subprocess, so the core stays light.
- Uniform ToolSpec: name, description, input_schema -> callable.
- Graceful degradation: a missing/unreachable server never crashes a node.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)


class MCPError(RuntimeError):
    pass


@dataclass
class ToolSpec:
    """A callable tool exposed to agent nodes."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    handler: Callable[..., Any] | None = None

    def call(self, arguments: dict[str, Any]) -> Any:
        if self.handler is None:
            raise MCPError(f"Tool '{self.name}' has no handler")
        return self.handler(**arguments)


# ---------------------------------------------------------------------- #
# Transport: stdio MCP server (JSON-RPC 2.0 over stdin/stdout)
# ---------------------------------------------------------------------- #
class StdioMCPClient:
    """Spawn an MCP server as a subprocess and speak JSON-RPC over stdio.

    Implements initialize -> tools/list -> tools/call lifecycle.
    """

    def __init__(self, name: str, command: str, args: list[str] | None = None) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self._proc: subprocess.Popen | None = None
        self._request_id = 0

    # -- lifecycle ------------------------------------------------------ #
    def start(self) -> None:
        executable = shutil.which(self.command)
        if not executable:
            raise MCPError(f"MCP server '{self.name}': command not found: {self.command}")
        self._proc = subprocess.Popen(
            [executable, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    # -- JSON-RPC -------------------------------------------------------- #
    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._proc is None or self._proc.poll() is not None:
            raise MCPError(f"MCP server '{self.name}' not running")
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise MCPError(f"MCP server '{self.name}' closed stdout")
        resp = json.loads(line)
        if "error" in resp:
            raise MCPError(f"MCP '{self.name}' {method} error: {resp['error']}")
        return resp.get("result", {})

    def initialize(self) -> dict[str, Any]:
        return self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "eywalink-orchestration", "version": "0.1.0"},
        })

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._request("tools/list", {})
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content", [])
        texts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(texts) if texts else result


# ---------------------------------------------------------------------- #
# Transport: HTTP MCP server (streamable HTTP transport, JSON-RPC POST)
# ---------------------------------------------------------------------- #
class HTTPMCPClient:
    """Talk to an MCP server over HTTP (POST JSON-RPC)."""

    def __init__(self, name: str, url: str, headers: dict[str, str] | None = None) -> None:
        self.name = name
        self.url = url
        self._client = httpx.Client(
            headers={"Content-Type": "application/json", **(headers or {})},
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0),
        )
        self._request_id = 0

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        try:
            resp = self._client.post(self.url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MCPError(f"MCP HTTP '{self.name}' {method} failed: {exc}") from exc
        if isinstance(data, list):  # SSE batch responses
            data = data[0] if data else {}
        if "error" in data:
            raise MCPError(f"MCP HTTP '{self.name}' {method} error: {data['error']}")
        return data.get("result", {})

    def initialize(self) -> dict[str, Any]:
        return self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "eywalink-orchestration", "version": "0.1.0"},
        })

    def list_tools(self) -> list[dict[str, Any]]:
        return self._request("tools/list", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content", [])
        texts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(texts) if texts else result


# ---------------------------------------------------------------------- #
# Registry
# ---------------------------------------------------------------------- #
class MCPRegistry:
    """Loads MCP servers from config and exposes a flat tool catalog.

    config format::

        tools:
          mcp_servers:
            - name: filesystem
              transport: stdio
              command: npx
              args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
            - name: remote
              transport: http
              url: "http://localhost:9100/mcp"
              headers: {}
    """

    def __init__(self, servers_config: list[dict[str, Any]] | None = None) -> None:
        self.servers_config = servers_config or []
        self._clients: list[Any] = []
        self._tools: dict[str, ToolSpec] = {}
        self._errors: list[str] = []

    def connect_all(self) -> "MCPRegistry":
        """Connect to every configured server; failures are recorded, not raised."""
        for cfg in self.servers_config:
            name = cfg.get("name", "unnamed")
            try:
                transport = cfg.get("transport", "stdio")
                if transport == "http":
                    client = HTTPMCPClient(name, cfg["url"], cfg.get("headers"))
                else:
                    client = StdioMCPClient(
                        name, cfg.get("command", ""), cfg.get("args") or []
                    )
                    client.start()
                client.initialize()
                self._clients.append(client)
                for tool in client.list_tools():
                    spec = ToolSpec(
                        name=f"{name}__{tool['name']}",
                        description=tool.get("description", ""),
                        input_schema=tool.get("inputSchema", {}),
                        handler=lambda _c=client, _t=tool["name"], **kw: _c.call_tool(_t, kw),
                    )
                    self._tools[spec.name] = spec
                logger.info("MCP server '%s' connected (%d tools)", name, len(self._tools))
            except (MCPError, KeyError, httpx.HTTPError) as exc:
                msg = f"MCP server '{name}' unavailable: {exc}"
                self._errors.append(msg)
                logger.warning(msg)
        return self

    def register_local(self, spec: ToolSpec) -> None:
        """Register a built-in/local tool (no server required)."""
        self._tools[spec.name] = spec

    def list_tools(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def tool_names(self) -> list[str]:
        return sorted(self._tools)

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        spec = self._tools.get(name)
        if spec is None:
            raise MCPError(f"Unknown tool: {name}")
        return spec.call(arguments)

    def close(self) -> None:
        for client in self._clients:
            try:
                client.stop()
            except Exception:  # noqa: BLE001
                pass
        self._clients.clear()

    def __enter__(self) -> "MCPRegistry":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
