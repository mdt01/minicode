# minicode

**minicode** is a small coding agent you build yourself while reading
*The Modern Software Developer*. It starts in Chapter 3 as a loop of about 200 lines and
grows, chapter by chapter, into a miniature version of a production coding agent.

| Version | Chapter | What it adds |
|---|---|---|
| v0.1 | 3 | Agent loop, three file tools, a simple text-based tool protocol (`minicode/v01_text_protocol.py`) |
| v0.2 | 3 | Native tool calling, tool schemas from Python functions, a guarded shell tool (`minicode/agent.py`) |
| later | 4–18 | System prompt and plan mode, MCP, skills, hooks, subagents, background mode ... |

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
Commands: `/tokens` shows token usage, `/save` writes the full conversation to
`session.json`, `/exit` quits.
Risky shell commands (deleting files, installing packages, git push ...) always ask for
your approval; dangerous ones are blocked.

> minicode is a teaching project. Run it only on code you have backed up with Git.

## Licence

MIT
