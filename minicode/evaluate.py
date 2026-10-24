"""Evaluate a tool set:  python -m minicode.evaluate tasks.json --workspace ../campushub

tasks.json is a list of tasks such as
  {"name": "this week", "prompt": "What is on campus this week?",
   "expect_tools": ["upcoming_events"], "expect_answer": ["Open-Mic"]}

Each task runs in a fresh agent. The report shows, per task and in total: success,
number of tool calls, tool errors (wrong tool or wrong arguments) and input tokens.
"""
import argparse
import json
from pathlib import Path


def tools_used(messages: list[dict]) -> list[str]:
    """Names of the tools called, looking through call_tool to the real tool."""
    names = []
    for message in messages:
        for call in message.get("tool_calls") or []:
            name = call["function"]["name"]
            if name == "call_tool":
                name = json.loads(call["function"]["arguments"] or "{}").get("name", name)
            names.append(name)
    return names


def run_task(agent, task: dict) -> dict:
    answer = agent.run_turn(task["prompt"])
    used = tools_used(agent.messages)
    errors = sum(1 for m in agent.messages
                 if m["role"] == "tool" and str(m["content"]).startswith("Error"))
    ok = (all(any(e in u for u in used) for e in task.get("expect_tools", []))
          and all(e.lower() in answer.lower() for e in task.get("expect_answer", [])))
    return {"task": task["name"], "success": ok, "calls": len(used), "errors": errors,
            "input_tokens": agent.tokens["input"]}


def report(results: list[dict]) -> str:
    lines = [f"{'task':<28}{'ok':>4}{'calls':>7}{'errors':>8}{'input tok':>11}"]
    for r in results:
        lines.append(f"{r['task'][:27]:<28}{'yes' if r['success'] else 'NO':>4}"
                     f"{r['calls']:>7}{r['errors']:>8}{r['input_tokens']:>11,}")
    n = len(results) or 1
    lines.append(f"{'TOTAL':<28}{sum(r['success'] for r in results):>3}/{len(results)}"
                 f"{sum(r['calls'] for r in results) / n:>6.1f}"
                 f"{sum(r['errors'] for r in results) / n:>8.1f}"
                 f"{sum(r['input_tokens'] for r in results) // n:>11,}   (averages)")
    return "\n".join(lines)


def main() -> None:
    from minicode.agent import Agent, make_client
    from minicode.mcp_client import connect_all
    from minicode.tools import Workspace

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tasks")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--tool-search", action="store_true")
    parser.add_argument("--runs", type=int, default=1, help="repeat each task")
    args = parser.parse_args()

    client, model = make_client()
    workspace = Workspace(args.workspace, confirm=lambda _: False)   # never risky commands
    servers = connect_all(workspace.root, on_event=lambda _: None)
    results = []
    for task in json.loads(Path(args.tasks).read_text(encoding="utf-8")) * args.runs:
        agent = Agent(client, model, workspace, on_event=lambda _: None)
        for server in servers:
            agent.add_tools(server.agent_tools(), deferred=args.tool_search)
        results.append(run_task(agent, task))
    print(report(results))


if __name__ == "__main__":
    main()
