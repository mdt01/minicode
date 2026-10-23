"""minicode v0.1: the smallest useful coding agent (text-based tool protocol).

The model asks for a tool by writing a line such as
    <tool name="read_file">{"path": "app/main.py"}</tool>
Our program finds that tag, runs the tool and sends back the result.
Run:  python -m minicode.v01_text_protocol --workspace ../campushub
"""
import argparse
import inspect
import json
import re

from minicode.agent import make_client
from minicode.tools import ToolError, Workspace

TOOL_TAG = re.compile(r'<tool name="(\w+)">(.*?)</tool>', re.DOTALL)

PROMPT = """You are a coding assistant working in the folder {root}.
You can use these tools:

{tool_list}
To use a tool, reply with exactly one line in this form and nothing else:
<tool name="TOOL_NAME">{{"argument": "value"}}</tool>
You will receive the result in a message starting with TOOL RESULT.
When the task is finished, reply normally without any tool tag."""


def describe(tools: dict) -> str:
    """Turn each function's name, signature and docstring into text for the prompt."""
    parts = []
    for name, fn in tools.items():
        parts.append(f"- {name}{inspect.signature(fn)}\n  {inspect.getdoc(fn)}\n")
    return "\n".join(parts)


def find_tool_call(text: str) -> tuple[str, dict] | None:
    match = TOOL_TAG.search(text)
    if not match:
        return None
    try:
        return match.group(1), json.loads(match.group(2))
    except json.JSONDecodeError:
        return match.group(1), {"__invalid__": match.group(2)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=".")
    args = parser.parse_args()
    client, model = make_client()
    workspace = Workspace(args.workspace)
    tools = {k: v for k, v in workspace.tools().items() if k != "run_bash"}   # no shell in v0.1
    messages = [{"role": "system", "content": PROMPT.format(root=workspace.root, tool_list=describe(tools))}]

    while True:
        try:
            messages.append({"role": "user", "content": input("you> ")})
        except (EOFError, KeyboardInterrupt):
            break
        while True:                                   # inner loop: keep going while tools are requested
            reply = client.chat.completions.create(model=model, messages=messages)
            text = reply.choices[0].message.content or ""
            messages.append({"role": "assistant", "content": text})
            call = find_tool_call(text)
            if call is None:
                print(f"\nassistant> {text}\n")
                break
            name, arguments = call
            print(f"  -> {name}({arguments})")
            try:
                result = tools[name](**arguments)
            except (KeyError, TypeError, ToolError) as exc:
                result = f"Error: {exc}"
            messages.append({"role": "user", "content": f"TOOL RESULT for {name}:\n{result}"})


if __name__ == "__main__":
    main()
