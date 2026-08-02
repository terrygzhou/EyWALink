# EyWALink Orchestration — API Contracts

Version: 0.1.0 · All payloads JSON unless noted · Model endpoint is
OpenAI-compatible (`/v1/chat/completions`).

## 1. Pipeline config (YAML)

```yaml
pipeline:
  name: software-build
  max_steps: 50 # int; HITL loop guard too
  sequential: true # reserved; only sequential is supported today
  llm:
    base_url: http://localhost:8080 # required
    model: Qwen3.6-27B-NVFP4 # required
    temperature: 0.2
    max_tokens: 4096
    timeout_read: 600 # seconds

agents:
  pm: { enabled: true }
  architect: { enabled: true }
  coder: { enabled: true, chunk_size: 1 } # files per generation request
  qa: { enabled: true }

mcp: # optional; MCP tool servers
  servers:
    - name: my-server
      command: npx
      args: ['-y', '@scope/mcp-server']
      # env: {...}, cwd: "/path"        # optional

persistence:
  status_file: pipeline-status.json
```

Loading: `load_pipeline_config(path)` deep-merges user YAML over
`DEFAULT_CONFIG`; missing `llm.base_url` / `llm.model` raise `ValueError`.

## 2. Pipeline state (JSON — the durable run record)

```json
{
  "goal": "Build a CLI that summarizes markdown files",
  "pipeline_name": "default",
  "phase": "implementation",
  "status": "running",
  "error": null,
  "run_id": "…",
  "review_feedback": null,
  "reviews_used": 0,
  "messages": [{ "role": "system", "content": "…" }],
  "agent_outputs": [{ "agent": "agent_pm", "artifact": "spec", "title": "…" }],
  "artifacts": {
    "spec": {
      "title": "…",
      "summary": "…",
      "requirements": [],
      "acceptance_criteria": [],
      "out_of_scope": []
    },
    "architecture": { "overview": "…", "components": [], "file_plan": [], "risks": [] },
    "code": {
      "files": [{ "path": "src/app.py", "language": "python", "content": "…" }],
      "chunk_size": 1,
      "failures": []
    },
    "tests": { "test_plan": [], "test_files": [] },
    "qa_report": { "passed": true, "checks": [], "files_validated": 1 }
  },
  "tokens_used": 0,
  "steps_completed": 4
}
```

- Persisted via `save_state(state, path)` — atomic temp-file + rename.
- `load_state(path)` returns `None` when the file is missing.

## 3. LLM client contract

`LLMClient(base_url, model, *, api_key="not-needed", temperature=0.2,
max_tokens=4096, transport=None)`

- `await complete(messages, **overrides) -> str` — assistant text.
- `await complete_message(messages, **overrides) -> dict` — full message
  object (includes `tool_calls` when tools are passed).
- `await complete_json(messages, **overrides) -> dict` — JSON mode with
  lenient parsing (raw JSON, then fenced-block fallback).
- Errors: `LLMError` base; `LLMTimeoutError` on read timeout (600s default).

Request shape sent to the model server:

```json
POST /v1/chat/completions
{
  "model": "…", "messages": [{"role": "system", "content": "…"}],
  "temperature": 0.2, "max_tokens": 4096, "stream": false,
  "tools": [{"type": "function", "function": {"name": "…", "description": "…", "parameters": {}}}]
}
```

## 4. Agent node contract

Every node: `async fn(state: PipelineState, ctx: AgentContext) -> dict`
returning a **partial update**. Nodes never mutate input state.

`AgentContext`: `llm: LLMClient`, `config: dict`, `workdir: Path|None`,
`max_steps: int` (default 50). Build from config:
`AgentContext.from_config(config, workdir=…)`.

Per-agent artifact keys:

| Node              | Writes `artifacts[...]`                  | Requires                                         |
| ----------------- | ---------------------------------------- | ------------------------------------------------ |
| `agent_pm`        | `spec`                                   | `goal`                                           |
| `agent_architect` | `architecture`                           | `artifacts.spec`                                 |
| `agent_coder`     | `code` (+ files on disk under `workdir`) | `artifacts.architecture.file_plan`               |
| `agent_qa`        | `tests`, `qa_report`                     | `artifacts.code` (validates; blocks on bad code) |

## 5. MCP integration contract

- `ToolRegistry.register(name, fn, *, description="", input_schema=None)` —
  local tool; `fn` is `async (args: dict) -> Any`.
- `await registry.import_server(client)` — import all tools from an
  `MCPServerClient`; returns imported names.
- `registry.schema()` — OpenAI function schema for LLM tool binding.
- `await registry.call(name, args)` — `KeyError` on unknown name.
- `MCPServerClient(command, args=None, *, env=None, cwd=None)` — async
  context manager; `list_tools() -> list[MCPToolSpec]`,
  `call_tool(name, args) -> Any` (coalesces text content blocks; JSON
  decoded when parseable).
- `run_tool_loop(llm, registry, messages, *, max_rounds=8) -> str` —
  raises `LLMError` when the model keeps calling tools past `max_rounds`.

## 6. Human-in-the-loop contract

`build_reviewed_pipeline_graph(ctx, *, review_after="agent_coder",
checkpointer=None) -> CompiledStateGraph`

Invoke:

```python
graph = build_reviewed_pipeline_graph(ctx)
result = await graph.ainvoke(make_initial_state(goal),
                             config={"configurable": {"thread_id": "run-1"}})
# result["__interrupt__"][0].value == human_review payload
```

Resume:

```python
await graph.ainvoke(Command(resume={"decision": "approve"}),
                    config={"configurable": {"thread_id": "run-1"}})
# or
await graph.ainvoke(Command(resume={"decision": "request_changes",
                                    "feedback": "add type hints"}),
                    config={"configurable": {"thread_id": "run-1"}})
```

Interrupt payload:

```json
{
  "type": "human_review",
  "phase": "implementation",
  "review_after": "agent_coder",
  "summary": { "spec": {}, "architecture": {}, "code_files": ["src/app.py"] }
}
```

## 7. Error taxonomy

| Exception           | Raised by     | Meaning                      |
| ------------------- | ------------- | ---------------------------- |
| `LLMError`          | llm / mcp     | LLM or MCP call failed       |
| `LLMTimeoutError`   | llm           | read timeout exceeded        |
| `ValueError`        | config / hitl | bad config or `review_after` |
| `FileNotFoundError` | config        | pipeline YAML missing        |
| `KeyError`          | ToolRegistry  | unknown tool name            |
