"""Command-line interface:  python -m minicode --workspace ../campushub"""
import argparse
import json

from minicode import __version__
from minicode.agent import NOTES_FILE, Agent, make_client
from minicode import commands
from minicode.context import meter, zone
from minicode.mcp_client import connect_all
from minicode.tools import Workspace
from minicode.tracing import TracingClient

HELP = """Commands:
  /plan          plan mode: read-only exploration, then a written plan
  /go            approve the plan and implement it
  /compact [focus]  summarize the conversation to free context
  /context       what fills the context window, and which zone you are in
  /clear         forget the conversation and start fresh
  /handoff       write progress notes to disk, then start fresh from them
  /model NAME    switch model (strong for planning, lighter for routine work)
  /tools         list the tools the model can use (built-in and MCP)
  /tokens        show tokens used so far
  /save          write the conversation to session.json
  /exit          quit
  /NAME [args]   run a project command from .agents/commands/NAME.md"""


def main() -> None:
    parser = argparse.ArgumentParser(prog="minicode", description="A small coding agent.")
    parser.add_argument("--workspace", default=".", help="folder the agent may work in")
    parser.add_argument("--yes", action="store_true", help="approve risky commands automatically")
    parser.add_argument("--trace", help="append every request and response to this .jsonl file")
    parser.add_argument("--tool-search", action="store_true",
                        help="hide MCP tools behind search_tools/call_tool (saves context)")
    parser.add_argument("--context-limit", type=int, default=100_000,
                        help="context window of your model, in tokens")
    args = parser.parse_args()

    client, model = make_client()
    if args.trace:
        client = TracingClient(client, args.trace)
    workspace = Workspace(args.workspace, confirm=(lambda _: True) if args.yes else None)
    agent = Agent(client, model, workspace, context_limit=args.context_limit)
    servers = connect_all(workspace.root)                   # tools from .mcp.json
    for server in servers:
        agent.add_tools(server.agent_tools(), deferred=args.tool_search)
    print(f"minicode {__version__}  |  model: {model}  |  workspace: {workspace.root}")
    print(HELP)
    print(f"  project commands: {', '.join(commands.available(workspace.root)) or 'none'}\n")
    while True:
        try:
            text = input(f"you{' [plan]' if agent.mode == 'plan' else ''}> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        command, _, rest = text.partition(" ")
        if command in {"/exit", "/quit"}:
            break
        elif command == "/plan":
            agent.mode = "plan"
            print("  plan mode on: describe the change you want planned")
        elif command == "/go":
            print(f"\nminicode> {agent.approve_plan()}\n")
        elif command == "/compact":
            agent.compact(focus=f"\nFocus especially on: {rest}" if rest else "")
        elif command == "/context":
            show_context(agent)
        elif command == "/clear":
            agent.clear()
            print("  conversation cleared; project rules will be re-sent")
        elif command == "/handoff":
            agent.handoff()
            print(f"  handoff note saved to {NOTES_FILE}; fresh session started")
        elif command == "/model":
            agent.model = rest or agent.model
            print(f"  model: {agent.model}")
        elif command == "/tools":
            names = list(agent.active_tools())
            print("  built-in: " + ", ".join(n for n in names if not n.startswith("mcp__")))
            for name in (n for n in names if n.startswith("mcp__")):
                print(f"  {name}")
        elif command == "/tokens":
            print(f"  tokens so far: {agent.tokens}")
        elif command == "/save":
            with open("session.json", "w", encoding="utf-8") as f:
                json.dump(agent.messages, f, indent=2)
            print("  saved the full conversation to session.json")
        elif command == "/help":
            print(HELP)
        elif text:
            if command.startswith("/"):                     # a project command?
                prompt = commands.expand(workspace.root, command[1:], rest)
                if prompt is None:
                    print(f"  unknown command {command}; type /help")
                    continue
                text = prompt
            print(f"\nminicode> {agent.run_turn(text)}\n")
            used = agent.context_used()
            if used > agent.context_limit / 2:              # warn when leaving the smart zone
                print(f"  [context {100 * used / agent.context_limit:.0f}% - "
                      f"{zone(used, agent.context_limit)}]")


def show_context(agent: Agent) -> None:
    """A context meter: estimated tokens per category, and the current zone."""
    used, limit = agent.context_used(), agent.context_limit
    for name, count in meter(agent.request_preview()):
        print(f"  {name:<26}{count:>8,}")
    print(f"  {'total':<26}{used:>8,} of {limit:,} ({100 * used / limit:.0f}%)")
    print(f"  {zone(used, limit)}; auto-compact at {agent.compact_at:.0%}")


if __name__ == "__main__":
    main()
