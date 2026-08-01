# EyWALink Orchestration — API Contracts

Version: 0.1.0 · Scope: EYW-3 (LangGraph core)

## 1. Python API

### PipelineState (TypedDict)

```python
class PipelineState(TypedDict):
    project_name: str
    objective: str
    mode: NotRequired[str]                       # "auto" | "interactive"
    requirements_doc: Annotated[str, last_value]
    architecture_doc: Annotated[str, last_value]
    code_files: Annotated[dict[str, str], append_dict]
    qa_report: Annotated[str, last_value]
    req_approved: Annotated[bool, last_value]
    qa_passed: Annotated[bool, last_value]
    rework_count: Annotated[int, last_value]
    human_feedback: Annotated[str, append_feedback]
    stage_errors: NotRequired[list[str]]
```

### build_graph(ctx: NodeContext) -> CompiledStateGraph

Builds and compiles the StateGraph with a MemorySaver checkpointer.

- Nodes: `pm`, `architect`, `coder`, `qa`, `human_gate`
- Edges: `START→pm→architect→coder→qa→human_gate`; gate routes `END` or back to `architect`
- Requires `config.configurable.thread_id` on every `invoke()`

### run_pipeline(ctx, project_name, objective, mode="auto", thread_id="default") -> dict

Convenience wrapper. `mode="auto"` runs end-to-end without pauses; `mode="interactive"`
pauses at the human gate.

### Interactive resume contract

1. First `graph.invoke(initial_state, config)` with `mode="interactive"` returns
   state containing `"__interrupt__": [Interrupt(value={...})]` — **no exception**
   is raised in langgraph 1.2.x.
2. Resume: `graph.invoke(Command(resume=[{"approved": bool, "feedback": str}]), config)`
   with the **same** `thread_id`.
3. The gate unwraps the list payload (`resume[0]`) — feedback and approval are
   honored; rejection routes to architect rework (bounded by `MAX_REWORK = 2`).

```python
from langgraph.types import Command

first = graph.invoke(initial, config)          # contains __interrupt__
result = graph.invoke(
    Command(resume=[{"approved": True, "feedback": "ship it"}]),
    config,                                     # same thread_id
)
```

### NodeContext

```python
@dataclass
class NodeContext:
    llm: LLMClient
    config: dict = field(default_factory=dict)
    mcp: MCPRegistry | None = None
    output_dir: Path | None = None              # coder persists files here when set
```

### LLMClient

```python
LLMClient(base_url, model, api_key="", max_tokens=32768, temperature=0.7,
          timeout=httpx.Timeout(connect=30, read=600, write=60, pool=30),
          max_retries=2)

client.chat(messages, *, max_tokens=None, temperature=None, response_format=None) -> str
client.chat_text(prompt, system="", **kwargs) -> str
client.chat_json(prompt, system="", **kwargs) -> dict   # 3-stage extraction, {} on failure
LLMClient.from_config(cfg) -> LLMClient                 # cfg → env → defaults
```

Errors: raises `LLMError` after retries; `chat_json` never raises (returns `{}`).

## 2. Config contract (pipeline-config.yaml)

```yaml
pipeline: {name: str, objective: str}
llm:
  base_url: str          # OpenAI-compatible /v1 endpoint
  model: str             # use the id from GET /v1/models
  api_key: str           # "" for local
  max_tokens: int        # default 32768
  temperature: float
  read_timeout: float    # default 600 (httpx read timeout fix)
  connect_timeout: float # default 30
  max_retries: int       # default 2
state: {file: str, persist_artifacts: bool}
tools:
  mcp_servers:
    - {name: str, transport: "stdio"|"http", command?: str, args?: [str], url?: str, headers?: {str: str}}
gates: {max_rework: int}  # default 2
```

`load_config(path)` deep-merges over `DEFAULT_CONFIG`; raises `ConfigError` on
missing/malformed file.

## 3. Persistence contract (pipeline-status.json)

```json
{"schema_version": 1, "state": { ...PipelineState... }}
```

- `save_state(path, state)` — writes atomically via temp file, creates parents
- `load_state(path)` — returns `None` on missing/corrupt (never raises)

## 4. MCP layer contract

```python
class ToolSpec:      # name, description, input_schema, handler
class MCPRegistry:
    def connect_all(self) -> MCPRegistry     # logs, never raises
    def register_local(self, spec) -> None
    def list_tools(self) -> list[ToolSpec]
    def tool_names(self) -> list[str]
    def call(self, name, arguments) -> Any   # raises MCPError on unknown tool
    def close(self) -> None
```

Tool naming: `{server}__{tool}` (e.g. `filesystem__read_file`). Server failures
are recorded in `registry._errors` and logged; pipeline nodes continue.

## 5. Behavior contract

- Nodes are pure-ish: read `PipelineState`, return partial updates. Never mutate
  the input dict.
- `coder` with `output_dir` set writes each file to `<output_dir>/<filepath>`
  immediately after generation (chunked, per-file).
- QA verdict detection: `"APPROVED" in report.upper()[:200]`.
- Rework loop is bounded by `MAX_REWORK`; after exhaustion the gate ships
  regardless (with `req_approved=False`).
