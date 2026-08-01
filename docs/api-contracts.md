# EyWALink Orchestration — API Contracts (v1)

> Source of truth: `packages/orchestration/src/eywalink_orchestration/`.
> The public surface is `eywalink_orchestration` — everything callers depend
> on is plain JSON/YAML data; no LangGraph types leak out (zero lock-in).

## 1. PipelineConfig (YAML)

Loaded by `load_pipeline_config(path)`; validated and deep-merged with
defaults. Required keys come from the **user's** YAML — defaults never
silently mask a missing LLM endpoint.

| Key | Required | Default | Notes |
|---|---|---|---|
| `pipeline.name` | no | `default` | run label |
| `pipeline.max_steps` | no | `100` | safety cap on pipeline steps |
| `pipeline.sequential` | no | `true` | always true in v1 (VRAM budget) |
| `pipeline.llm.base_url` | **yes** | — | SGLang / vLLM / Ollama endpoint |
| `pipeline.llm.model` | **yes** | — | model name on that server |
| `pipeline.llm.temperature` | no | `0.2` | |
| `pipeline.llm.max_tokens` | no | `4096` | |
| `pipeline.llm.timeout_read` | no | `600` | seconds; local LLMs are slow |
| `agents.<name>.enabled` | no | `true` | per-agent toggles |
| `agents.coder.chunk_size` | no | `1` | files per code generation request |
| `persistence.status_file` | no | `pipeline-status.json` | durable JSON record |
| `mcp.servers` | no | `[]` | `[{name, command, args, env?, cwd?}]` |

Errors: `ValueError("pipeline.llm.base_url is required ...")` when the LLM
endpoint is missing; `yaml.YAMLError` on invalid YAML; `FileNotFoundError`
when the file is absent.

Example: `packages/orchestration/examples/pipeline-config.yaml`.

## 2. PipelineState (JSON)

The LangGraph `TypedDict` (see `state.py`) serialized to
`persistence.status_file` as plain JSON. Key fields:

| Field | Reducer | Semantics |
|---|---|---|
| `goal` | last-write-wins | user goal |
| `phase` | last-write-wins | `init → requirements → architecture → implementation → qa → review → done` |
| `status` | last-write-wins | `pending / running / blocked / done / failed` |
| `artifacts` | dict-merge | per-agent sections: `spec`, `architecture`, `code`, `tests`, `qa_report` |
| `messages` | append | conversation/agent trace |
| `agent_outputs` | append | per-agent record: `{agent, artifact, ...}` |
| `steps_completed` | add | progress counter |
| `reviews_used` | add | HITL review rounds consumed |
| `tokens_used` | add | cumulative token accounting |

Serialization is atomic (temp file + rename) so a crash never corrupts the
last checkpoint. `load_state` returns `None` for a missing file.

## 3. Runtime operations

Thin facade in `api.py`. All functions return/accept plain JSON-compatible
data. The LangGraph checkpoint (SQLite, next to `status_file`) makes
pause/resume durable across calls and processes.

### `run_pipeline(goal, config, *, workdir=None, status_file=..., thread_id="run-1", review=True, auto_approve=False) -> dict`

Executes `PM -> Architect -> Coder -> QA` (plus the review gate when
`review=True`). Returns the final or paused state dict and persists it.

### `get_status(status_file=...) -> dict`

Reads the durable run record: `{status, phase, goal, steps_completed,
reviews_used, error, run_id, updated_at}`. Returns
`{"status": "not_found", ...}` when no run exists yet.

### `resume(decision, *, feedback=None, config=..., status_file=..., thread_id="run-1") -> dict`

Continues a paused run at the human review gate.
- `decision="approve"` — mark the run `done`.
- `decision="request_changes"` with `feedback` — loop back to the Coder.

`config` is required (the graph rebuilds with the same LLM binding).

### `get_artifacts(status_file=...) -> dict | None`

Returns the `artifacts` dict of the persisted run, or `None`.

## 4. Error contract

`LLMError` / `LLMTimeoutError` from `llm.py` bubble to the node runner, which
sets `status=failed` + `error` and stops the graph. An LLM JSON response
shaped `{"error": ...}` is treated as a failure by `complete_json` and stops
the run the same way. Tool errors during the MCP loop are surfaced to the
model as tool results (not fatal).

## 5. MCP tool layer (v1)

- `ToolRegistry` — register local tools or import remote MCP tools by name.
- `MCPServerClient` — async stdio client to a local MCP server subprocess
  (`initialize` / `tools/list` / `tools/call`).
- `mcp_servers_from_config(config)` — build clients from `mcp.servers`.
- `run_tool_loop(llm, registry, messages, max_rounds=8)` — LLM + tools
  conversation loop; the model emits `tool_calls`, results are fed back.

v1 scope: **local stdio MCP servers only**; remote/HTTP MCP servers deferred.
Tool results are appended to `agent_outputs` / message history; agents must
summarize before writing to `artifacts`.
