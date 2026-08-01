"""Tests for the MCP tool integration layer.

Covers the local ``ToolRegistry``, the LLM tool-use loop against a mocked
OpenAI-compatible server (``httpx.MockTransport``), and the stdio MCP
client against a real MCP server spawned in-process (``mcp.server.fastmcp``
is available because ``mcp`` is a project dependency).
"""

from __future__ import annotations

import json

import httpx
import pytest

from eywalink_orchestration import LLMClient, ToolRegistry, run_tool_loop
from eywalink_orchestration.mcp import MCPServerClient, mcp_servers_from_config

# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


async def _echo(args: dict) -> dict:
    return {"echo": args}


def test_register_and_schema() -> None:
    reg = ToolRegistry()
    reg.register("echo", _echo, description="Echo args back", input_schema={"type": "object"})
    assert reg.names() == ["echo"]
    schema = reg.schema()
    assert schema[0]["type"] == "function"
    assert schema[0]["function"]["name"] == "echo"
    assert schema[0]["function"]["description"] == "Echo args back"


def test_call_unknown_tool_raises() -> None:
    reg = ToolRegistry()
    with pytest.raises(KeyError):
        import asyncio

        asyncio.run(reg.call("nope", {}))


@pytest.mark.asyncio
async def test_call_local_tool() -> None:
    reg = ToolRegistry()
    reg.register("echo", _echo)
    assert await reg.call("echo", {"a": 1}) == {"echo": {"a": 1}}


# ---------------------------------------------------------------------------
# LLM tool-use loop (mocked server)
# ---------------------------------------------------------------------------


def _tool_loop_handler() -> httpx.MockTransport:
    """Round 1: ask for a tool call; round 2: answer without tools."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        has_tool_result = any(m.get("role") == "tool" for m in payload["messages"])
        # No tool result yet => ask the model for a tool call.
        if not has_tool_result:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "echo",
                                            "arguments": json.dumps({"msg": "hi"}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
                request=request,
            )
        # The model sees the tool result and answers.
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "tool said hi"}}
                ]
            },
            request=request,
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_run_tool_loop_executes_and_returns() -> None:
    llm = LLMClient("http://llm:8080", "test-model", transport=_tool_loop_handler())
    reg = ToolRegistry()
    reg.register("echo", _echo)
    answer = await run_tool_loop(
        llm,
        reg,
        [{"role": "user", "content": "use echo"}],
    )
    assert answer == "tool said hi"


@pytest.mark.asyncio
async def test_run_tool_loop_caps_rounds() -> None:
    """A model that never stops calling tools hits max_rounds."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_x",
                                    "type": "function",
                                    "function": {
                                        "name": "echo",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            request=request,
        )

    llm = LLMClient("http://llm:8080", "test-model", transport=httpx.MockTransport(handler))
    reg = ToolRegistry()
    reg.register("echo", _echo)
    from eywalink_orchestration.llm import LLMError

    with pytest.raises(LLMError, match="max_rounds"):
        await run_tool_loop(
            llm,
            reg,
            [{"role": "user", "content": "loop forever"}],
            max_rounds=2,
        )


# ---------------------------------------------------------------------------
# MCP stdio client (in-process server)
# ---------------------------------------------------------------------------


def _fastmcp_server() -> str:
    """Python source for a tiny MCP server exposing one tool."""
    return """
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("test-server")

@mcp.tool()
def add(a: int, b: int) -> int:
    \"\"\"Add two integers.\"\"\"
    return a + b

if __name__ == "__main__":
    mcp.run()
"""


@pytest.mark.asyncio
async def test_mcp_stdio_client_lists_and_calls(tmp_path) -> None:
    import sys

    server_path = tmp_path / "fastmcp_server.py"
    server_path.write_text(_fastmcp_server(), encoding="utf-8")

    async with MCPServerClient(
        sys.executable, [str(server_path)], env={"PYTHONPATH": ":".join(sys.path)}
    ) as server:
        tools = await server.list_tools()
        assert any(t.name == "add" for t in tools)

        result = await server.call_tool("add", {"a": 2, "b": 3})
        assert result == 5


@pytest.mark.asyncio
async def test_mcp_import_into_registry(tmp_path) -> None:
    import sys

    server_path = tmp_path / "fastmcp_server.py"
    server_path.write_text(_fastmcp_server(), encoding="utf-8")

    reg = ToolRegistry()
    async with MCPServerClient(
        sys.executable, [str(server_path)], env={"PYTHONPATH": ":".join(sys.path)}
    ) as server:
        imported = await reg.import_server(server)
        assert imported == ["add"]

    # After closing the server the registry still routes by name, but the
    # underlying call fails because the subprocess is gone — the registry is
    # a routing layer, not a transport.
    assert "add" in reg.names()


def test_mcp_servers_from_config() -> None:
    cfg = {
        "mcp": {
            "servers": [
                {"name": "github", "command": "npx", "args": ["-y", "@x/github"]}
            ]
        }
    }
    clients = mcp_servers_from_config(cfg)
    assert len(clients) == 1
    assert clients[0].command == "npx"
    assert clients[0].args == ["-y", "@x/github"]
