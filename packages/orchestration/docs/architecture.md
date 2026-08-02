# EyWALink Orchestration — Architecture

Status: implemented (v0.1.0) · Scope: `packages/orchestration`

This document describes the core agent orchestration engine: how a
goal becomes a verified software deliverable through a sequence of agent
nodes, where human review fits, and how MCP tools plug in.

## 1. Design principles

1. **Zero lock-in.** Every interface is an open standard: OpenAI-compatible
   chat API (SGLang / vLLM / Ollama), MCP for tools, YAML for config, JSON
   for state. No vendor SDK in the hot path.
2. **Budget-friendly by construction.** One model server, sequential
   execution, chunked generation. The pipeline never fans out requests that
   would OOM a single GPU or blow a token budget.
3. **Crash-resilient.** State is a plain JSON document persisted atomically;
   the LangGraph checkpoint adds interrupt/resume for human-in-the-loop.
4. **Deterministic failure routing.** A failed or blocked node stops the
   pipeline — no downstream work on broken input.

## 2. Components

```
packages/orchestration/src/eywalink_orchestration/
├── state.py         PipelineState TypedDict + Annotated reducers
├── llm.py           LLMClient — OpenAI-compatible chat (httpx, long timeouts)
├── config.py        YAML pipeline config loader (deep-merged defaults)
├── persistence.py   atomic JSON state save/load
├── agents/          four agent nodes (PM, Architect, Coder, QA) + AgentContext
├── graph.py         sequential pipeline graph (compile + routing)
├── hitl.py          human-in-the-loop review gate (interrupt/resume)
└── mcp.py           MCP tool integration layer (registry, stdio client, tool loop)
```

## 3. State machine

`PipelineState` is a LangGraph `TypedDict` with **explicit reducers**
declared via `typing.Annotated` (LangGraph reads them from the schema):

| Field             | Reducer           | Semantics                          |
| ----------------- | ----------------- | ---------------------------------- |
| `messages`        | `operator.add`    | append conversation trace          |
| `agent_outputs`   | `operator.add`    | append per-node trace entries      |
| `artifacts`       | `merge_artifacts` | dict-merge keyed by artifact name  |
| `tokens_used`     | `operator.add`    | accumulate                         |
| `steps_completed` | `operator.add`    | accumulate (used for failure caps) |
| `reviews_used`    | `operator.add`    | accumulate (HITL loop guard)       |
| scalars           | last-write-wins   | `goal`, `phase`, `status`, `error` |

Phases: `init → requirements → architecture → implementation → qa → done`
(plus `review` during HITL). Status: `pending | running | blocked | done |
failed`.

### Sequential pipeline (`graph.py`)

`PM → Architect → Coder → QA`, strictly sequential. Each node returns a
_partial state update_ (plain dict); reducers decide append vs overwrite, so
nodes never mutate the incoming state. After every node, a router checks
`status`; `failed`/`blocked` routes to `END`.

### Human-in-the-loop (`hitl.py`)

`build_reviewed_pipeline_graph()` inserts a **review gate** after a
configurable node (default: the Coder). The review node calls
`langgraph.types.interrupt()` with a summary of the produced artifacts
(spec, architecture, code file list). Execution suspends; a human (via CLI
or gateway API) resumes with:

- `approve` → continue to QA (clears any outstanding feedback)
- `request_changes` + feedback → Coder re-runs with feedback appended to
  its goal; bounded by `max_steps` review rounds.

Persistence: compiled with a checkpointer (`InMemorySaver` by default).
Swap in a durable checkpoint backend for cross-process approval queues.

## 4. MCP tool integration (`mcp.py`)

Two layers:

- **ToolRegistry** — register local tools and import remote MCP tools into
  one execution surface. Exposes OpenAI-style function schema for the LLM.
- **MCPServerClient** — stdio transport to an MCP server subprocess
  (`initialize` → `tools/list` → `tools/call`), fully async.

`run_tool_loop(llm, registry, messages)` drives the agentic loop: the model
emits `tool_calls`, the registry executes them (local or remote — nodes
can't tell the difference), results feed back as `tool` messages, until the
model answers. Capped by `max_rounds`.

MCP servers are configured in `pipeline-config.yaml` under `mcp.servers`
(`command` + `args`), loaded by `mcp_servers_from_config()`.

## 5. Failure modes

| Failure                          | Behaviour                                                  |
| -------------------------------- | ---------------------------------------------------------- |
| LLM timeout (600s read)          | node returns `failed`, router stops pipeline               |
| Empty goal at PM                 | `failed` immediately                                       |
| Coder: empty file plan           | `failed` (nothing to code)                                 |
| Coder: LLM returns empty content | recorded as failure, pipeline continues to QA which blocks |
| QA static validation fails       | `blocked`, no further work                                 |
| HITL changes exceed `max_steps`  | `failed` (loop guard)                                      |

## 6. Operations notes

- Run the test suite: `cd packages/orchestration && uv run pytest`
- Install: `uv sync` (brings up `mcp` SDK for the tool layer)
- Local model server default: `http://localhost:8080` (SGLang), model
  `Qwen3.6-27B-NVFP4` — override via `pipeline.llm` in YAML.

## 7. Open questions / next steps

- Durable checkpoint backend (SQLite/Postgres) for production HITL.
- Streaming token accounting (currently counted via `tokens_used` only).
- MCP over HTTP/SSE transport (stdio implemented; HTTP is the same
  JSON-RPC surface).
