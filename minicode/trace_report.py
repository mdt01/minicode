"""Show what fills a request:  python -m minicode.trace_report trace.jsonl [--call N]

Works with traces from minicode (--trace) and from tools/trace_proxy.py, in either the
OpenAI or the Anthropic request format. Token counts are estimates (4 characters each).
"""
import argparse
import json
import re

REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)


def tokens(value) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return len(text) // 4


def text_of(content) -> str:
    """Message content may be a string or a list of blocks; flatten it to text."""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


def breakdown(request: dict) -> dict[str, int]:
    parts = {"system prompt": tokens(request.get("system", ""))}   # Anthropic format
    tools = request.get("tools") or []
    mcp = [t for t in tools if tool_name(t).startswith("mcp__")]
    parts[f"tools: built-in ({len(tools) - len(mcp)})"] = sum(tokens(t) for t in tools
                                                              if t not in mcp)
    parts[f"tools: MCP ({len(mcp)})"] = sum(tokens(t) for t in mcp)
    for message in request.get("messages", []):
        role, text = message.get("role", "?"), text_of(message.get("content") or "")
        if role == "system":                                       # OpenAI format
            parts["system prompt"] += tokens(text)
            continue
        injected = sum(tokens(r) for r in REMINDER.findall(text))
        parts["harness reminders"] = parts.get("harness reminders", 0) + injected
        key = f"messages: {role}"
        parts[key] = parts.get(key, 0) + tokens(text) - injected
        if message.get("tool_calls"):
            parts[key] += tokens(message["tool_calls"])
    return parts


def tool_name(tool: dict) -> str:
    return tool.get("name") or tool.get("function", {}).get("name", "")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trace")
    parser.add_argument("--call", type=int, default=-1, help="which request (default: last)")
    args = parser.parse_args()
    with open(args.trace, encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]
    request = records[args.call]["request"]
    parts = breakdown(request)
    total = sum(parts.values()) or 1
    print(f"{len(records)} requests in trace; showing request {args.call % len(records) + 1}")
    print(f"model: {request.get('model')}\n")
    for name, count in sorted(parts.items(), key=lambda p: -p[1]):
        if not count:
            continue
        bar = "#" * round(40 * count / total)
        print(f"{name:<28}{count:>8,} tokens {100 * count / total:5.1f}%  {bar}")
    print(f"{'total (estimate)':<28}{total:>8,} tokens")


if __name__ == "__main__":
    main()
