# EyWALink Orchestration — Architecture

Status: implemented (v0.1.0) · Scope: EYW-3 (LangGraph core)

## 1. Framework selection

**LangGraph (StateGraph API)** — chosen over CrewAI, AutoGen, and a hand-rolled FSM:

- Explicit nodes/edges → auditable topology, visualizable (`get_graph().draw_ascii()`)
- Typed shared state with reducer-controlled merge semantics
- First-class human-in-the-loop (`interrupt()` / `Command(resume=...)`)
- Checkpointer persistence (MemorySaver now, SqliteSaver for production)
- Apache-2.0, self-hostable, zero lock-in — matches EyWALink's mission

Rejected: CrewAI (heavier abstraction, less control), AutoGen (conversation-centric,
overkill for deterministic pipelines), hand-rolled FSM (no checkpoints, no HIL primitives).

## 2. Workflow

```
START -> pm -> architect -> coder -> qa -> human_gate -> END
                                            |            ^
                                            +-> architect (rework, max 2)
```

- **pm** — requirements doc (scope, functional/NFR, acceptance criteria)
- **architect** — technical design (components, data flow, module layout, APIs)
- **coder** — chunked, file-by-file code generation (never one giant blob)
- **qa** — code review + APPROVED/REJECTED verdict
- **human_gate** — LangGraph `interrupt()`; approve → END, reject → architect rework

All nodes run **sequentially** — no parallel execution. Local LLMs share VRAM;
concurrent requests degrade throughput and cause timeouts.

## 3. State schema (PipelineState)

TypedDict with explicit reducers (`state.py`):

| Field | Reducer | Semantics |
|-------|---------|-----------|
| requirements_doc | last_value | single-writer artifact |
| architecture_doc | last_value | single-writer artifact |
| code_files | append_dict | merged across rework cycles |
| qa_report | last_value | latest QA output |
| human_feedback | append_feedback | accumulated across reworks |
| req_approved / qa_passed / rework_count | last_value | gate flags + loop guard |

Without explicit reducers, LangGraph's default shallow merge would overwrite
accumulated feedback and files on every rework pass.

## 4. LLM client

`LLMClient` (`llm.py`) — minimal OpenAI-compatible client over httpx:

- **httpx timeout fix**: explicit `httpx.Timeout(connect=30, read=600, write=60, pool=30)`.
  httpx's default 5s READ timeout silently kills local generations even when a
  higher `timeout` is set at the SDK layer.
- `max_tokens=32768` default — reasoning models (Qwen3.6) emit a long thinking
  block first; low caps truncate before any usable content.
- `chat_json()` 3-stage extraction (direct → fences → every-`{` bracket match)
  with a string-aware brace matcher to survive wrapped/nested JSON from
  reasoning models.
- Null-content retry: vLLM can return `content: null`; retried, never crashes.
- Config precedence: YAML `llm:` section → env (`LLM_BASE_URL`, `LLM_MODEL`,
  `LLM_API_KEY`) → defaults.

## 5. MCP tool integration layer

`MCPRegistry` (`mcp.py`) — zero-lock-in MCP client with two transports:

- **stdio** (`StdioMCPClient`): spawn server subprocess, JSON-RPC 2.0 over stdin/stdout
- **http** (`HTTPMCPClient`): POST JSON-RPC to a remote MCP endpoint

Lifecycle: `connect_all()` → initialize → tools/list → tools/call. Tools are
exposed as `ToolSpec(name, description, input_schema, handler)` in a flat
registry. **Graceful degradation**: an unreachable server is logged and
recorded in `registry._errors` — it never crashes a pipeline node. Local
built-in tools can be registered alongside MCP tools via `register_local()`.

Config (`pipeline-config.yaml`):

```yaml
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
```

## 6. Persistence & resume

`persistence.py` — `save_state`/`load_state` for `pipeline-status.json`
(schema_version 1). A new session loads the last state and resumes without
re-doing completed stages. Corrupt/missing files return `None` (fresh start),
never raise.

## 7. Config

Single `pipeline-config.yaml` drives the pipeline (`config.py`): pipeline
identity, LLM endpoint/model/timeouts, state file path, MCP servers, gate
limits. Deep-merged over `DEFAULT_CONFIG` so partial files work.

## 8. Key decisions & trade-offs

| Decision | Why | Trade-off |
|----------|-----|-----------|
| Sequential nodes | VRAM contention on local LLM | No parallel speedup |
| Node-returned `Command(goto=...)` | langgraph 1.2.x conditional-edge paths must return plain names; node Command carries update+route atomically | Slightly less visual in graph dumps |
| `interrupt()` HIL (not raise-based) | langgraph 1.2.x records `__interrupt__` in state; resume via `Command(resume=[...])` | Caller must handle `__interrupt__` key |
| MemorySaver | Zero-config dev/test checkpointer | Use SqliteSaver for multi-process prod |
| Chunked codegen | Single full-codebase call times out on local models | More LLM round-trips |

## 9. Known limits / next steps

- Graph nodes call the LLM directly; MCP tools are wired but not yet consumed
  by nodes by default (next: give coder/qa nodes a tool-using loop).
- Checkpointer is in-memory; production swap to SqliteSaver (custom impl —
  `langgraph.checkpoint.sqlite` was removed in 1.0+).
- No auth/tenant isolation yet — single-tenant pipeline engine.
- EYW-8 follow-ups: richer agent prompts, tool-using coder loop, per-stage
  output validation.
