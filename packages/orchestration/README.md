# EyWALink Orchestration Core

LangGraph-based agent orchestration primitives — the foundation of EyWALink's
agent platform. Zero lock-in: OpenAI-compatible chat API, YAML config, JSON
persistence, and open-source frameworks only.

## Components

- `state.py` — `PipelineState` TypedDict with explicit `Annotated` reducers
  (append for traces, dict-merge for artifacts, add for counters).
- `llm.py` — `LLMClient` with resilient httpx timeouts (read=600s,
  connect=30s) tuned for local model serving on shared VRAM.
- `config.py` — YAML pipeline config loader with deep-merged defaults.
- `persistence.py` — atomic JSON state save/load for crash-resilient runs.
- `agents/` — the four core agent nodes:
  - `agent_pm` — requirements gathering and spec generation
  - `agent_architect` — technical design and architecture doc
  - `agent_coder` — chunked code generation (file-by-file, never whole repo)
  - `agent_qa` — test generation + deterministic static validation
- `graph.py` — `build_pipeline_graph()`: sequential `PM -> Architect ->
  Coder -> QA` wiring with failure routing (no parallel requests — VRAM
  contention on local model servers).
- `hitl.py` — `build_reviewed_pipeline_graph()`: human review gate
  **between QA and done** (LangGraph interrupt + checkpointer-backed
  resume; approve → done, request_changes → back to Coder).
- `api.py` — stable contract surface: `run_pipeline`, `get_status`,
  `resume`, `get_artifacts` (plain JSON in/out, durable SQLite checkpoints).
- `mcp.py` — MCP tool integration layer: `ToolRegistry`, `MCPServerClient`
  (local stdio servers), `run_tool_loop`.

## Install

```bash
uv sync          # or: uv pip install -e .
uv run pytest    # run the test suite
```

## Minimal usage

```python
from eywalink_orchestration import (
    AgentContext, LLMClient, build_pipeline_graph, make_initial_state,
)

state = make_initial_state("Build a demo CLI", pipeline_name="demo")
ctx = AgentContext(
    llm=LLMClient("http://localhost:8080", "Qwen3.6-27B-NVFP4"),
    workdir="generated/demo",   # coder + QA write files here
)

graph = build_pipeline_graph(ctx)
final = await graph.ainvoke(state)
print(final["artifacts"]["qa_report"])
```

Run the four agents individually (useful for debugging):

```python
from eywalink_orchestration.agents import agent_pm, make_node

node = make_node("agent_pm", agent_pm, ctx)
partial = await node(state)  # returns a partial state update dict
```

## Human-in-the-loop review

```python
from eywalink_orchestration import build_reviewed_pipeline_graph, review_resume_approve

graph = build_reviewed_pipeline_graph(ctx)
thread = {"configurable": {"thread_id": "run-1"}}

result = await graph.ainvoke(make_initial_state("Build a CLI"), config=thread)
# result["__interrupt__"] — pipeline paused for human review after QA

final = await graph.ainvoke(review_resume_approve(), config=thread)
```

For durable cross-process resume use the API facade:

```python
from eywalink_orchestration import get_artifacts, get_status, resume, run_pipeline

run_id_state = await run_pipeline("Build a CLI", "pipeline-config.yaml", thread_id="run-1")
# paused at the review gate
status = get_status()                       # {"status": "running", ...}
final = await resume("approve", config="pipeline-config.yaml", thread_id="run-1")
artifacts = get_artifacts()
```

## MCP tools

```python
from eywalink_orchestration import MCPServerClient, ToolRegistry, run_tool_loop

reg = ToolRegistry()
async with MCPServerClient("npx", ["-y", "@modelcontextprotocol/server-github"]) as srv:
    await reg.import_server(srv)          # import remote tools
    answer = await run_tool_loop(llm, reg, [{"role": "user", "content": "…"}])
```

## Docs

- Architecture: [docs/architecture.md](docs/architecture.md)
- API contracts: [docs/api-contracts.md](docs/api-contracts.md)

The package is layout `src/` with `hatchling`, requires Python >= 3.12.
