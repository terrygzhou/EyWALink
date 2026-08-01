# EyWALink Orchestration

LangGraph core for EyWALink — zero lock-in, budget-friendly enterprise
private AI. Multi-agent pipeline (PM → Architect → Coder → QA) with a
human-in-the-loop gate, MCP tool integration, and state persistence for
session resume. Built for local LLMs (vLLM / SGLang) with sequential
execution only.

## Layout

```
src/eywalink_orchestration/
  state.py        PipelineState TypedDict + explicit reducers
  llm.py          LLMClient (httpx timeout fix: read=600s, connect=30s)
  config.py       YAML config parser (pipeline-config.yaml)
  persistence.py  State persistence (pipeline-status.json)
  mcp.py          MCP tool integration layer (stdio + HTTP transports)
  agents.py       Agent nodes: pm, architect, coder (chunked), qa
  graph.py        LangGraph StateGraph + HIL gate + routing
tests/            Offline unit + graph wiring tests (stubbed LLM)
```

## Quick start

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest
```

Run against a local OpenAI-compatible endpoint (vLLM/SGLang):

```python
from eydwalink_orchestration.config import load_config
from eydwalink_orchestration.llm import LLMClient
from eydwalink_orchestration.agents import NodeContext
from eydwalink_orchestration.graph import run_pipeline

cfg = load_config("pipeline-config.yaml")
ctx = NodeContext(llm=LLMClient.from_config(cfg["llm"]), config=cfg)
result = run_pipeline(ctx, "demo", "Build a demo service", mode="auto")
```

Interactive mode pauses at the human gate; resume with
`Command(resume=[{"approved": True, "feedback": "..."}])`.

## Key design decisions

- **StateGraph, not functional API** — explicit nodes/edges, auditable.
- **Sequential execution** — no parallel nodes (local LLM VRAM contention).
- **Explicit reducers** — feedback/files accumulate across rework cycles.
- **Chunked codegen** — file-by-file, never one giant JSON blob (timeouts).
- **HIL via interrupt()** — compiled with auto_approve=False; auto mode for CI.
- **Graceful MCP degradation** — unreachable servers are logged, not fatal.

See `docs/architecture.md` and `docs/api-contracts.md` for details.
