"""Command-line interface:  python -m minicode --workspace ../campushub"""
import argparse
import json

from minicode import __version__
from minicode.agent import Agent, make_client
from minicode.tools import Workspace


def main() -> None:
    parser = argparse.ArgumentParser(prog="minicode", description="A small coding agent.")
    parser.add_argument("--workspace", default=".", help="folder the agent may work in")
    parser.add_argument("--yes", action="store_true", help="approve risky shell commands automatically")
    args = parser.parse_args()

    client, model = make_client()
    workspace = Workspace(args.workspace, confirm=(lambda _: True) if args.yes else None)
    agent = Agent(client, model, workspace)
    print(f"minicode {__version__}  |  model: {model}  |  workspace: {workspace.root}")
    print("Type a request. Commands: /tokens, /save, /exit\n")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text in {"/exit", "/quit"}:
            break
        if text == "/save":
            with open("session.json", "w", encoding="utf-8") as f:
                json.dump(agent.messages, f, indent=2)
            print("  saved the full conversation to session.json")
            continue
        if text == "/tokens":
            print(f"  tokens so far: {agent.tokens}")
            continue
        if text:
            print(f"\nminicode> {agent.run_turn(text)}\n")


if __name__ == "__main__":
    main()
