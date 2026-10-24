"""minicode v0.9: subagents - helpers with a fresh context, defined in .agents/agents/*.md.

    ---
    name: reviewer
    description: Reviews the current diff against a plan. Use after implementing.
    tools: read_file, list_files, run_bash
    ---
    You are a careful code reviewer. ...

Only the subagent's final message returns to the main agent.
"""
from pathlib import Path

from minicode.skills import parse_skill

AGENT_DIR = Path(".agents") / "agents"


def load_definitions(root: Path) -> dict[str, dict]:
    definitions = {}
    folder = root / AGENT_DIR
    for file in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        meta = parse_skill(file)                      # same front matter format
        meta["name"] = meta["name"] if meta["name"] != folder.name else file.stem
        tools = meta.get("tools", "")
        meta["tools"] = [t.strip() for t in tools.split(",") if t.strip()] or None
        definitions[meta["name"]] = meta
    return definitions


def delegate_schema(definitions: dict[str, dict]) -> dict:
    """The delegate tool's description lists the available subagents."""
    listing = "\n".join(f"- {d['name']}: {d['description']}" for d in definitions.values())
    return {"type": "function", "function": {
        "name": "delegate",
        "description": ("Hand a self-contained task to a subagent with a fresh context. "
                        "It sees ONLY the task text, so include the goal, relevant files, "
                        "constraints and the exact form of the answer you need. Only its "
                        f"final message comes back.\nSubagents:\n{listing}"),
        "parameters": {"type": "object", "required": ["agent", "task"], "properties": {
            "agent": {"type": "string", "enum": list(definitions)},
            "task": {"type": "string"}}}}}
