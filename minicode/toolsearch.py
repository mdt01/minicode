"""minicode v0.7: tool search - load tool definitions only when the model asks for them.

With many MCP servers, sending every tool definition on every request wastes context and
confuses the model. In search mode the model sees just two tools, search_tools and
call_tool, and discovers the rest on demand (the "search and execute" pattern).
"""
import json
import re
from typing import Callable


class ToolCatalog:
    def __init__(self) -> None:
        self.entries: dict[str, tuple[Callable, dict]] = {}

    def add(self, tools: dict[str, tuple[Callable, dict]]) -> None:
        self.entries.update(tools)

    def search_tools(self, query: str, limit: int = 5) -> str:
        """Find tools for a task. Returns up to `limit` matching tools with their argument
        schemas. Search with plain words, e.g. "upcoming events" or "register student".
        Then use call_tool with one of the returned names."""
        words = set(re.findall(r"[a-z0-9]+", query.lower()))
        scored = []
        for name, (_, schema) in self.entries.items():
            text = (name + " " + schema["function"]["description"]).lower()
            score = sum(1 for w in words if w in text)
            if score:
                scored.append((score, name, schema["function"]))
        if not scored:
            return f"No tools match '{query}'. Try other words. {len(self.entries)} tools exist."
        scored.sort(key=lambda s: (-s[0], s[1]))
        return "\n".join(json.dumps({"name": n, "description": f["description"],
                                     "parameters": f["parameters"]})
                         for _, n, f in scored[:limit])

    def call_tool(self, name: str, arguments: str = "{}") -> str:
        """Run a tool found with search_tools. `arguments` is a JSON object string,
        e.g. '{"days": 7}'."""
        if name not in self.entries:
            return f"Error: no tool named '{name}'. Use search_tools to find it."
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return "Error: arguments must be a JSON object string, e.g. '{\"days\": 7}'."
        function, _ = self.entries[name]
        return str(function(**args))
