"""Evaluate a tool set:  python -m minicode.evaluate tasks.json --workspace ../campushub

tasks.json is a list of tasks such as
  {"name": "this week", "prompt": "What is on campus this week?",
   "expect_tools": ["upcoming_events"], "expect_answer": ["Open-Mic"]}

Each task runs in a fresh agent. The report shows, per task and in total: success,
number of tool calls, tool errors (wrong tool or wrong arguments) and input tokens.
An expected answer may list alternatives: "not found|does not exist".

The agents may NOT edit files or run shell commands (found in Lab 8: an agent evaluating a
"register" task wrote a test into the project). Use --allow-edits to evaluate coding tasks.
"""
import argparse
import json
import subprocess
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
          and all(answer_contains(answer, e) for e in task.get("expect_answer", [])))
    return {"task": task["name"], "success": ok, "calls": len(used), "errors": errors,
            "input_tokens": agent.tokens["input"], "answer": answer}


def answer_contains(answer: str, expected: str) -> bool:
    """True if the answer contains the expected text, or one of its "a|b" alternatives."""
    return any(option.strip().lower() in answer.lower() for option in expected.split("|"))


def evaluation_policy(root: Path, allow_edits: bool = False):
    """The project's permission rules, plus: no file edits and no shell, unless allowed."""
    from minicode.security import Policy
    policy = Policy(root, "ask")
    if not allow_edits:
        policy.rules["deny"] += ["edit_file", "run_bash"]
    return policy


def workspace_state(root: Path) -> set[str]:
    """Changed and untracked files according to git (empty if this is not a git repository)."""
    done = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True,
                          text=True)
    return set(done.stdout.splitlines()) if done.returncode == 0 else set()


def report(results: list[dict], verbose: bool = False) -> str:
    lines = [f"{'task':<28}{'ok':>4}{'calls':>7}{'errors':>8}{'input tok':>11}"]
    for r in results:
        lines.append(f"{r['task'][:27]:<28}{'yes' if r['success'] else 'NO':>4}"
                     f"{r['calls']:>7}{r['errors']:>8}{r['input_tokens']:>11,}")
    n = len(results) or 1
    lines.append(f"{'TOTAL':<28}{sum(r['success'] for r in results):>3}/{len(results)}"
                 f"{sum(r['calls'] for r in results) / n:>6.1f}"
                 f"{sum(r['errors'] for r in results) / n:>8.1f}"
                 f"{sum(r['input_tokens'] for r in results) // n:>11,}   (averages)")
    if verbose:
        for r in results:
            if not r["success"]:
                lines.append(f"\n{r['task']} (NO) answered:\n  " +
                             r.get("answer", "").strip().replace("\n", "\n  ")[:600])
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
    parser.add_argument("--allow-edits", action="store_true",
                        help="let the agents edit files and run commands (coding tasks)")
    parser.add_argument("--verbose", action="store_true", help="show answers of failed tasks")
    args = parser.parse_args()

    client, model = make_client("evaluate")
    workspace = Workspace(args.workspace, confirm=lambda _: False)   # never risky commands
    servers = connect_all(workspace.root, on_event=lambda _: None)
    before = workspace_state(workspace.root)
    results = []
    for task in json.loads(Path(args.tasks).read_text(encoding="utf-8")) * args.runs:
        agent = Agent(client, model, workspace, on_event=lambda _: None)
        agent.policy = evaluation_policy(workspace.root, args.allow_edits)
        for server in servers:
            agent.add_tools(server.agent_tools(), deferred=args.tool_search)
        results.append(run_task(agent, task))
    print(report(results, verbose=args.verbose))
    changed = sorted(workspace_state(workspace.root) - before)
    if changed:
        print("\nThe evaluation changed files in the workspace (check, then keep or discard):")
        print("\n".join(f"  {line}" for line in changed))


if __name__ == "__main__":
    main()
