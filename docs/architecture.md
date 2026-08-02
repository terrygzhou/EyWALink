# EyWALink Agent Orchestration Platform — Architecture & API Contracts

> Issue EYW-3 · Owner: CEO · Status: active · Framework: LangGraph (selected)

## 1. Context & Mission Fit

EyWALink delivers zero lock-in, budget-friendly enterprise private AI. The
orchestration platform is the control plane that lets customers compose
multi-agent pipelines that run entirely on their own hardware (SGLang / vLLM /
Ollama on a single GPU), with no vendor dependency.

**Framework selection: LangGraph (confirmed).** Rationale:

- Explicit state machine (nodes + edges + reducers) matches our sequential,
  human-in-the-loop pipeline model exactly.
- `TypedDict` state with per-field reducers gives compile-time-ish safety and
  deterministic merge semantics for multi-agent partial updates.
- First-class human-in-the-loop interrupts — required for review gates.
- Open source (MIT), no lock-in; runs against any OpenAI-compatible endpoint.
- Rejected alternatives: CrewAI (higher-level but less explicit state control,
  opinionated abstractions), AutoGen (conversation-centric, heavier),
  hand-rolled asyncio pipeline (no checkpointing/interrupts).

## 2. System Context

```
┌─────────────────────────────────────────────────────────────┐
│  packages/orchestration (Python, uv)                        │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐ │
│  │ config.py    │──▶│ graph.py     │──▶│ persistence.py   │ │
│  │ YAML -> dict │   │ StateGraph   │   │ pipeline-status  │ │
│  └──────────────┘   └──────────────┘   └──────────────────┘ │
│  ┌──────────────┐   ┌──────────────┐                        │
│  │ state.py     │   │ llm.py       │  OpenAI-compatible     │
│  │ PipelineState│   │ LLMClient    │──▶ SGLang/vLLM/Ollama  │
│  └──────────────┘   └──────────────┘                        │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
  packages/gateway (FastAPI) — chat completions, fallback chain,
  Qdrant vectors, Prometheus metrics, OTel traces
```

The orchestration package is the _agent_ control plane; the gateway is the
_LLM_ data plane. They communicate via the OpenAI-compatible API only.

## 3. Pipeline State Machine

Phases (from `state.py`): `init → requirements → architecture → implementation
→ qa → review → done`, with `blocked`/`failed` statuses for error paths.

**State fields and reducers:**

| Field                               | Reducer           | Semantics                                                     |
| ----------------------------------- | ----------------- | ------------------------------------------------------------- |
| `messages`                          | `operator.add`    | append conversation turns                                     |
| `agent_outputs`                     | `operator.add`    | append per-agent trace records                                |
| `artifacts`                         | `merge_artifacts` | dict-merge keyed by artifact name (each agent owns a section) |
| `tokens_used`                       | `operator.add`    | cumulative token accounting                                   |
| `steps_completed`                   | `operator.add`    | progress counter                                              |
| scalars (`goal`, `phase`, `status`) | last-write-wins   | single-writer sequential steps                                |

**Sequential execution is mandatory** — no parallel requests — because local
LLM inference on a single GPU contends for VRAM (one model resident at a time).
This is an explicit product constraint, not an optimization detail.

## 4. Agent Nodes (EYW-8)

| Node              | Input phase    | Produces (artifacts key)        |
| ----------------- | -------------- | ------------------------------- |
| `agent_pm`        | requirements   | `requirements.md` (spec)        |
| `agent_architect` | architecture   | `architecture.md`               |
| `agent_coder`     | implementation | `code/` (chunked, file-by-file) |
| `agent_qa`        | qa             | `tests.md` + validation results |

Each node: reads `PipelineState`, runs LLM via `LLMClient`, returns a partial
state update merged by the reducers above. Chunked generation is a hard rule
for `agent_coder`: one file per step, never a whole-codebase dump (LLM output
quality and VRAM budget).

## 5. Human-in-the-Loop

First end-to-end workflow includes a review gate **between `qa` and `done`**
(shipped in EYW-13):

- LangGraph interrupt at the review node (after `agent_qa`, before done).
- The run pauses with `status=running`; QA's validation outcome lives in
  `artifacts.qa_report.passed` (a failed validation stops the run as
  `blocked` before the gate).
- Reviewer (human) approves → `status=done`; requests changes → re-enters
  at the Coder with feedback, then QA, then the gate again.
- Implementation: `checkpointer` — `InMemorySaver` by default; the API
  facade (`api.py`) uses a durable SQLite-backed saver
  (`langgraph-checkpoint-sqlite`) tied to the status file, so resume works
  across processes. Pluggable for Postgres later.

## 6. MCP Tool Integration Layer

- Each agent node may declare MCP tools (filesystem, web, repo) via an MCP
  client; tools are injected as LangGraph `ToolNode`s in the node's subgraph.
- v1 scope: local stdio MCP servers only; remote/HTTP MCP servers deferred
  (matches the self-hosted, zero-lock-in posture).
- Contract: tool results are appended to `agent_outputs` and the calling
  agent's message history; no tool output is ever written directly to
  `artifacts` (agents must summarize).

## 7. API Contracts (v1)

- `PipelineConfig` (YAML): `pipeline.name`, `pipeline.goal`, `agents[].name`,
  `agents[].model`, `agents[].system_prompt`, `llm.base_url`, `llm.model`,
  `llm.timeout_seconds`, `checkpoint.dir`.
- `PipelineState` (JSON): the TypedDict shape above, serialized to
  `pipeline-status.json`.
- Runtime interface: `run_pipeline(config, initial_goal) -> run_id`,
  `get_status(run_id)`, `resume(run_id, approval)`, `get_artifacts(run_id)`.
- Error contract: `LLMError` / `LLMTimeoutError` from `llm.py` bubble to the
  node runner, which sets `status=failed` + `error` and stops the graph.

## 8. Delivery Plan

1. **EYW-7 (done):** state schema, LLM client, YAML config, persistence,
   round-trip test — core package skeleton. ✅ shipped.
2. **EYW-8 (done):** four agent nodes, sequential graph,
   first end-to-end pipeline run. ✅ shipped.
3. **EYW-3 close-out (done, EYW-13):** MCP tool layer (v1 local stdio
   servers), human-in-the-loop interrupt between QA and done, API contract
   tests (`test_api.py`), end-to-end demo (`examples/run_pipeline.py`),
   failing orchestration tests fixed. ✅ shipped.
4. **Deploy integration:** orchestration runs inside `packages/deploy` compose
   stack alongside gateway/SGLang/Qdrant; VRAM budget doc covers concurrency.
   Next up after close-out.

## 9. Open Questions

- Checkpointer backend: SQLite-on-disk is fine for v1; Postgres checkpointing
  should follow the gateway's DB choice.
- Resume semantics after failure: manual restart vs. retry-with-backoff.
- Where the orchestration worker process runs (long-lived supervisor vs.
  one-shot CLI) — decided for the demo (one-shot CLI); revisit for the HTTP
  API integration.
