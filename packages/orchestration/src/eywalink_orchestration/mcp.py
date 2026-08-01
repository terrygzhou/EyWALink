"""MCP tool integration layer.

Connects the orchestration platform to external tools through the Model
Context Protocol (MCP) — the open standard for tool/context exchange — plus
a local registry for in-process tools.

Layers:
- :class:`MCPToolSpec` — tool metadata (name, description, JSON Schema).
- :class:`ToolRegistry` — register tools (local or remote), expose the
  OpenAI-style function schema the LLM needs, and execute calls by name.
- :class:`MCPServerClient` — stdio transport to an MCP server subprocess
  (``initialize`` / ``tools/list`` / ``tools/call``), fully async.
- :func:`run_tool_loop` — drive an LLM + tools conversation loop: the model
  emits ``tool_calls``, the registry executes them, results are fed back,
  until the model answers without tool calls.

Zero lock-in: MCP itself is an open protocol; every tool is addressable by
name so pipelines never hard-code a vendor's function-calling format.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .llm import LLMClient, LLMError

logger = logging.getLogger(__name__)

#: Async tool implementation: ``(arguments: dict) -> result``.
ToolFn = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass
class MCPToolSpec:
    """Metadata describing one tool, in MCP/OpenAI-compatible terms."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    """Register tools and execute them by name.

    Tools come from two sources:
    - **Local** tools registered directly with :meth:`register`.
    - **Remote** MCP tools imported from a server via :meth:`import_server`.

    The registry is the single execution surface agent nodes use, so a
    pipeline can mix local utilities (filesystem, shell) and remote MCP
    capabilities without knowing which is which.
    """

    def __init__(self) -> None:
        self._tools: dict[str, MCPToolSpec] = {}
        self._fns: dict[str, ToolFn] = {}

    def register(
        self,
        name: str,
        fn: ToolFn,
        *,
        description: str = "",
        input_schema: dict[str, Any] | None = None,
    ) -> None:
        """Register a local tool implementation."""
        if not name or not callable(fn):
            raise ValueError("register requires a non-empty name and a callable")
        self._tools[name] = MCPToolSpec(
            name=name,
            description=description,
            input_schema=input_schema or {"type": "object", "properties": {}},
        )
        self._fns[name] = fn

    async def import_server(self, server: "MCPServerClient") -> list[str]:
        """Import all tools exposed by an MCP server into the registry.

        Returns the imported tool names. Remote tools execute through the
        server's ``tools/call`` method; the registry stores a thin async
        closure so agent nodes can't tell local from remote.
        """
        imported: list[str] = []
        for spec in await server.list_tools():
            self._tools[spec.name] = spec

            async def call(args: dict[str, Any], _name: str = spec.name) -> Any:
                return await server.call_tool(_name, args)

            self._fns[spec.name] = call
            imported.append(spec.name)
        return imported

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self) -> list[MCPToolSpec]:
        return [self._tools[n] for n in self.names()]

    def schema(self) -> list[dict[str, Any]]:
        """OpenAI function-calling schema for the LLM."""
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.input_schema,
                },
            }
            for spec in self.specs()
        ]

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Execute a tool by name."""
        fn = self._fns.get(name)
        if fn is None:
            raise KeyError(f"Unknown tool: {name!r} (have {self.names()})")
        return await fn(arguments or {})


# ---------------------------------------------------------------------------
# MCP stdio client
# ---------------------------------------------------------------------------


class MCPServerClient:
    """Async MCP client over stdio (server spawned as a subprocess).

    Typical usage::

        async with MCPServerClient("npx", ["-y", "@modelcontextprotocol/server-github"]) as srv:
            tools = await srv.list_tools()
            result = await srv.call_tool(tools[0].name, {"query": "..."})
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self.command = command
        self.args = list(args or [])
        self.env = env
        self.cwd = cwd
        self._session: Any = None
        self._stdio: Any = None

    async def __aenter__(self) -> "MCPServerClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def connect(self) -> None:
        """Spawn the server subprocess and negotiate an MCP session."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=self.env,
            cwd=self.cwd,
        )
        self._stdio = stdio_client(params)
        read, write = await self._stdio.__aenter__()
        self._session = await ClientSession(read, write).__aenter__()
        await self._session.initialize()
        logger.info("MCP server connected: %s %s", self.command, " ".join(self.args))

    async def list_tools(self) -> list[MCPToolSpec]:
        """Return the tools the server exposes."""
        if self._session is None:
            raise RuntimeError("MCPServerClient not connected")
        result = await self._session.list_tools()
        specs: list[MCPToolSpec] = []
        for tool in result.tools:
            schema: dict[str, Any] = dict(tool.inputSchema or {})
            specs.append(
                MCPToolSpec(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=schema,
                )
            )
        return specs

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a tool on the server and return its structured content."""
        if self._session is None:
            raise RuntimeError("MCPServerClient not connected")
        result = await self._session.call_tool(name, arguments or {})
        # MCP returns content blocks; coalesce text blocks into one payload.
        if result.isError:
            raise LLMError(f"MCP tool {name!r} returned an error: {result}")
        texts = [
            block.text
            for block in (result.content or [])
            if getattr(block, "type", None) == "text"
        ]
        raw = "\n".join(texts)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw

    async def aclose(self) -> None:
        """Tear down the session and subprocess."""
        if self._session is not None:
            try:
                await self._session.__aexit__(None, None, None)
            finally:
                self._session = None
        if self._stdio is not None:
            try:
                await self._stdio.__aexit__(None, None, None)
            finally:
                self._stdio = None


def mcp_servers_from_config(config: dict[str, Any]) -> list[MCPServerClient]:
    """Build MCP server clients from a pipeline config.

    Reads ``config["mcp"]["servers"]`` — a list of
    ``{"name": ..., "command": ..., "args": [...]}`` entries. Returns
    *unconnected* clients; call :meth:`MCPServerClient.connect` (or use
    ``async with``) before listing tools.
    """
    servers_cfg = (config.get("mcp") or {}).get("servers") or []
    clients: list[MCPServerClient] = []
    for entry in servers_cfg:
        clients.append(
            MCPServerClient(
                entry["command"],
                entry.get("args") or [],
                env=entry.get("env"),
                cwd=entry.get("cwd"),
            )
        )
    return clients


# ---------------------------------------------------------------------------
# LLM + tools loop
# ---------------------------------------------------------------------------


async def run_tool_loop(
    llm: LLMClient,
    registry: ToolRegistry,
    messages: list[dict[str, Any]],
    *,
    max_rounds: int = 8,
    **llm_kwargs: Any,
) -> str:
    """Run a tool-using conversation until the model stops calling tools.

    Each round: send messages + tool schema to the LLM; if the reply contains
    ``tool_calls``, execute each through the registry and append the results
    as ``tool`` messages; otherwise return the final assistant text.

    Args:
        llm: LLM client (OpenAI-compatible chat endpoint).
        registry: Tool registry exposing the tools the model may call.
        messages: Conversation so far (system + user turns).
        max_rounds: Safety cap on tool-call rounds.

    Returns:
        The model's final text answer after tool use.
    """
    tools = registry.schema()
    history = list(messages)
    for _ in range(max_rounds):
        reply = await llm.complete_message(
            history,
            tools=tools or None,
            **llm_kwargs,
        )
        calls = reply.get("tool_calls") or []
        if not calls:
            return reply.get("content") or ""
        # Execute each tool call and append results.
        for call in calls:
            fn_name = call["function"]["name"]
            try:
                args = json.loads(call["function"]["arguments"] or "{}")
                result = await registry.call(fn_name, args)
            except Exception as exc:  # noqa: BLE001 — surface to the model
                result = {"error": f"{type(exc).__name__}: {exc}"}
            history.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [call],
                }
            )
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result, default=str),
                }
            )
    raise LLMError(f"Tool loop exceeded max_rounds={max_rounds}")


__all__ = [
    "MCPServerClient",
    "MCPToolSpec",
    "ToolRegistry",
    "mcp_servers_from_config",
    "run_tool_loop",
]
