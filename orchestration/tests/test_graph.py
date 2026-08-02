"""Graph wiring and end-to-end pipeline tests with a stubbed LLM.

These tests do NOT call a real model — they use a FakeLLMClient that
returns deterministic content, so the suite runs offline and fast.
"""

from __future__ import annotations

from typing import Any

from eywalink_orchestration.agents import NodeContext
from eywalink_orchestration.graph import build_graph
from eywalink_orchestration.llm import LLMClient
from eywalink_orchestration.mcp import MCPRegistry, ToolSpec


class FakeLLMClient:
    """Deterministic stand-in for LLMClient."""

    model = "fake"

    def chat_text(self, prompt: str, system: str = "", **kwargs: Any) -> str:
        if "list the files" in prompt.lower():
            return '{"files": ["app/main.py", "app/config.py"]}'
        if "technical design" in prompt.lower():
            return "# Architecture\n- Component X\n- Component Y"
        if "review this code" in prompt.lower():
            return "APPROVED: code looks correct."
        if "single file" in prompt.lower():
            return "print('generated file')"
        if "requirements" in prompt.lower():
            return "# Requirements\n- Feature A\n- Feature B"
        return f"content for: {prompt[:40]}"

    def chat_json(self, prompt: str, system: str = "", **kwargs: Any) -> dict:
        if "list the files" in prompt.lower():
            return {"files": ["app/main.py", "app/config.py"]}
        return {}

    def close(self) -> None:
        pass


def _ctx(tmp_path, mode_registry=True) -> NodeContext:
    registry = None
    if mode_registry:
        registry = MCPRegistry([])
        registry.register_local(
            ToolSpec(
                name="fs_read",
                description="read a file",
                input_schema={},
                handler=lambda path: "file contents",
            )
        )
    return NodeContext(
        llm=FakeLLMClient(),  # type: ignore[arg-type]
        config={},
        mcp=registry,
        output_dir=tmp_path,
    )


def test_build_graph_compiles(tmp_path):
    graph = build_graph(_ctx(tmp_path))
    assert graph is not None


def test_end_to_end_auto_mode(tmp_path):
    graph = build_graph(_ctx(tmp_path))
    result = graph.invoke(
        {
            "project_name": "demo",
            "objective": "Build a demo service",
            "mode": "auto",
            "requirements_doc": "",
            "architecture_doc": "",
            "code_files": {},
            "qa_report": "",
            "req_approved": False,
            "qa_passed": False,
            "rework_count": 0,
            "human_feedback": "",
        },
        config={"configurable": {"thread_id": "e2e-1"}},
    )
    assert result["requirements_doc"].startswith("# Requirements")
    assert result["architecture_doc"].startswith("# Architecture")
    assert "app/main.py" in result["code_files"]
    assert "app/config.py" in result["code_files"]
    assert result["req_approved"] is True
    assert result["qa_passed"] is True
    # Coder persisted files to output dir
    assert (tmp_path / "app" / "main.py").exists()


def test_interactive_mode_pauses_at_gate_and_resumes(tmp_path):
    """Human-in-the-loop: interrupt() records __interrupt__; resume approves."""
    from langgraph.types import Command as LGCommand

    graph = build_graph(_ctx(tmp_path))
    config = {"configurable": {"thread_id": "hil-1"}}
    initial = {
        "project_name": "demo",
        "objective": "Build a demo service",
        "mode": "interactive",
        "requirements_doc": "",
        "architecture_doc": "",
        "code_files": {},
        "qa_report": "",
        "req_approved": False,
        "qa_passed": False,
        "rework_count": 0,
        "human_feedback": "",
    }
    # First invoke pauses at the gate and surfaces __interrupt__ in state
    first = graph.invoke(initial, config=config)
    assert "__interrupt__" in first
    assert first["req_approved"] is False

    # Resume with approval
    result = graph.invoke(
        LGCommand(resume=[{"approved": True, "feedback": "looks good"}]), config=config
    )
    assert result["req_approved"] is True
    assert result["qa_passed"] is True
    assert "looks good" in result["human_feedback"]


def test_interactive_mode_rework_loop(tmp_path):
    """Rejected gate sends back to architect, bounded by MAX_REWORK."""
    from langgraph.types import Command as LGCommand

    graph = build_graph(_ctx(tmp_path))
    config = {"configurable": {"thread_id": "hil-2"}}
    initial = {
        "project_name": "demo",
        "objective": "Build a demo service",
        "mode": "interactive",
        "requirements_doc": "",
        "architecture_doc": "",
        "code_files": {},
        "qa_report": "",
        "req_approved": False,
        "qa_passed": False,
        "rework_count": 0,
        "human_feedback": "",
    }
    first = graph.invoke(initial, config=config)
    assert "__interrupt__" in first

    # Reject once -> architect rework -> gate pauses again
    result = graph.invoke(
        LGCommand(resume=[{"approved": False, "feedback": "redesign needed"}]),
        config=config,
    )
    assert result["req_approved"] is False
    assert result["rework_count"] == 1
    assert "redesign needed" in result["human_feedback"]
    # Rework path re-enters architect and pauses at the gate again
    assert "__interrupt__" in result

    # Final approval ends the run
    final = graph.invoke(
        LGCommand(resume=[{"approved": True, "feedback": "ok now"}]),
        config=config,
    )
    assert final["req_approved"] is True
    assert final["rework_count"] == 1


def test_mcp_registry_local_tool():
    registry = MCPRegistry([])
    registry.register_local(
        ToolSpec(
            name="fs_read",
            description="read file",
            input_schema={},
            handler=lambda path: f"READ {path}",
        )
    )
    assert registry.tool_names() == ["fs_read"]
    assert registry.call("fs_read", {"path": "x"}) == "READ x"
    registry.close()


def test_mcp_unknown_tool_raises():
    import pytest

    from eywalink_orchestration.mcp import MCPError

    registry = MCPRegistry([])
    with pytest.raises(MCPError):
        registry.call("nope", {})
    registry.close()


def test_mcp_unavailable_server_degrades_gracefully():
    """A server whose command doesn't exist is recorded, not fatal."""
    registry = MCPRegistry(
        [{"name": "ghost", "transport": "stdio", "command": "definitely-not-a-cmd-xyz"}]
    )
    registry.connect_all()
    assert registry._errors
    assert registry.tool_names() == []
    registry.close()
