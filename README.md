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
| later | 6–18 | MCP, skills, hooks, subagents, background mode ... |

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
| `/tokens` | tokens used so far |
| `/save` | write the conversation to `session.json` |
| `/exit` | quit |

Useful options: `--trace trace.jsonl` records every request and response;
`--context-limit 32000` tells minicode your model's context window (compaction starts at 70%).

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
