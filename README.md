# minicode

**minicode** is a small coding agent you build yourself while reading
*The Modern Software Developer*. It starts in Chapter 3 as a loop of about 200 lines and
grows, chapter by chapter, into a miniature version of a production coding agent.

| Version | Chapter | What it adds |
|---|---|---|
| v0.1 | 3 | Agent loop, three file tools, a simple text-based tool protocol (`minicode/v01_text_protocol.py`) |
| v0.2 | 3 | Native tool calling, tool schemas from Python functions, a guarded shell tool (`minicode/agent.py`) |
| v0.3 | 4 | Structured system prompt, AGENTS.md injection, plan mode, compaction, request tracing (`prompts.py`, `compaction.py`, `tracing.py`) |
| v0.4 | 5 | Context meter and zones, `/clear`, `/handoff` progress notes, `/model` switching, compact instructions from AGENTS.md (`context.py`) |
| v0.5 | 6 | Project commands: reusable prompts in `.agents/commands/NAME.md` run as `/NAME args` (`commands.py`) |
| v0.6 | 7 | MCP client: tools from servers listed in `.mcp.json` appear as `mcp__server__tool`; `/tools` (`mcp_client.py`) |
| v0.7 | 8 | Tool search mode (`--tool-search`: `search_tools` + `call_tool`) and a tool-set evaluation harness (`python -m minicode.evaluate`) |
| v0.8 | 9 | Agent skills with progressive disclosure (`.agents/skills/`, `~/.minicode/skills/`), headless mode (`-p`, `--json`) (`skills.py`) |
| v0.9 | 10 | Nested and global agent files (`~/.minicode/AGENTS.md`, `app/AGENTS.md`), hooks (`.agents/hooks.json`: `pre_tool`, `post_tool`, `stop`), subagents (`.agents/agents/*.md`, `delegate` tool) (`hooks.py`, `subagents.py`) |
| v0.9.1 | 11 | Agent-readiness scorecard: 31 checks across 9 pillars and 5 maturity levels (`python -m minicode.readiness PATH --run-tests 5`) (`readiness.py`) |
| v0.10 | 12 | Verification gate: blocks deleted, skipped or weakened tests and requires a check command to pass before the agent may finish (`--verify CMD`, `--allow-test-changes`) (`verify.py`) |
| later | 13–18 | Review, security, background mode, loops ... |

## Setup

```bash
git clone https://github.com/mdt01/minicode.git
cd minicode
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q                          # runs without any API key
```

Choose a model through three environment variables (any OpenAI-compatible provider,
or a free local model with Ollama):

```bash
export LLM_BASE_URL=http://localhost:11434/v1   # or your provider's URL
export LLM_API_KEY=ollama                       # or your secret key
export LLM_MODEL=qwen2.5-coder:7b               # example model name
```

## Run it on CampusHub

```bash
python -m minicode --workspace ../campushub
```

Then type a request, for example: *"Explain how event registration works."*

| Command | What it does |
|---|---|
| `/plan` | plan mode: read-only exploration, then a plan in `.minicode/plan.md` |
| `/go` | approve the plan and implement it |
| `/compact [focus]` | summarize the conversation to free context |
| `/context` | what fills the context window, and which zone you are in |
| `/clear` | forget the conversation and start fresh |
| `/handoff` | write `.minicode/progress.md`, then start fresh from it |
| `/model NAME` | switch model (strong for planning, lighter for routine work) |
| `/tools` | list the tools the model can use (built-in and MCP) |
| `/tokens` | tokens used so far |
| `/save` | write the conversation to `session.json` |
| `/exit` | quit |
| `/NAME [args]` | run a project command (`.agents/commands/NAME.md`) or a skill (`.agents/skills/NAME/`) |

Useful options: `--trace trace.jsonl` records every request and response;
`--context-limit 32000` tells minicode your model's context window (compaction starts at 70%).

## Connect MCP servers

Put a `.mcp.json` file in the workspace folder; minicode connects at start-up:

```json
{"mcpServers": {
  "campushub": {"command": "python", "args": ["mcp_server/server.py"]},
  "remote":    {"url": "http://127.0.0.1:8765/mcp"}}}
```

Local servers are started as subprocesses (stdio); `url` entries use streamable HTTP.
CampusHub's own server is in the campushub repository (`mcp_server/`, Chapter 7).

## Skills

Skills are folders with a `SKILL.md` (front matter `name` and `description`, then instructions)
plus optional `scripts/` and `references/`. minicode reads them from `.agents/skills/` in the
workspace and from `~/.minicode/skills/`. The model sees only names and descriptions until it
calls `load_skill(name)`; bundled files are read with `read_skill_file`. Add
`disable-model-invocation: true` to make a skill user-invoked only (`/NAME`).

## Headless mode

```bash
python -m minicode --workspace ../campushub -p "List the API endpoints" --json
```

Runs one request and exits (0 = answered, 1 = error, 2 = step limit). Risky shell commands are
declined automatically unless you add `--yes`.

## Many tools? Search for them

`python -m minicode --tool-search` hides MCP tools behind two meta-tools, `search_tools` and
`call_tool`, so their definitions are loaded only when needed (Chapter 8).

## Evaluate a tool set

```bash
python -m minicode.evaluate tasks.json --workspace ../campushub --runs 3 [--tool-search]
```

`tasks.json` lists prompts with the tools and facts you expect; the report shows success,
tool calls, errors and input tokens per task.

## Look inside requests

```bash
python -m minicode.trace_report trace.jsonl          # what fills the last request?
python tools/trace_proxy.py --upstream https://api.openai.com --out trace.jsonl
```

`tools/trace_proxy.py` is a tiny logging proxy (standard library only). Point any agent's
base URL at `http://127.0.0.1:8080` to record its raw requests. API keys are never logged.
Risky shell commands (deleting files, installing packages, git push ...) always ask for
your approval; dangerous ones are blocked.

> minicode is a teaching project. Run it only on code you have backed up with Git.

## Licence

MIT
