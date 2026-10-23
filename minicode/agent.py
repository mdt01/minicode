"""minicode v0.2: the agent loop with native tool calling."""
import json
import os
from typing import Callable

from openai import OpenAI

from minicode.schema import all_schemas
from minicode.tools import ToolError, Workspace

SYSTEM_PROMPT = """You are minicode, a careful coding agent working in the folder {root}.
You can read files, list folders, edit files and run shell commands using your tools.

How to work:
- Explore before you change anything: list folders and read the relevant files.
- Make small, focused edits with edit_file. Never rewrite a whole file when a small edit will do.
- After changing code, run the tests (for example `pytest -q`) and fix what you broke.
- Do not touch files unrelated to the task.
- When you are done, reply with a short summary of what you changed and how you checked it.
"""


class Agent:
    def __init__(self, client: OpenAI, model: str, workspace: Workspace,
                 max_steps: int = 25, on_event: Callable[[str], None] = print):
        self.client = client
        self.model = model
        self.tools = workspace.tools()
        self.schemas = all_schemas(self.tools)
        self.max_steps = max_steps
        self.on_event = on_event
        self.tokens = {"input": 0, "output": 0}
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT.format(root=workspace.root)}]

    def run_turn(self, user_text: str) -> str:
        """Handle one user request: loop until the model stops asking for tools."""
        self.messages.append({"role": "user", "content": user_text})
        for _ in range(self.max_steps):
            response = self.client.chat.completions.create(
                model=self.model, messages=self.messages, tools=self.schemas)
            if response.usage:
                self.tokens["input"] += response.usage.prompt_tokens
                self.tokens["output"] += response.usage.completion_tokens
            message = response.choices[0].message
            self.messages.append(message.model_dump(exclude_none=True))
            if not message.tool_calls:
                return message.content or ""
            for call in message.tool_calls:
                result = self.execute(call.function.name, call.function.arguments)
                self.messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
        return "Stopped: reached the step limit. Ask me to continue if needed."

    def execute(self, name: str, arguments: str) -> str:
        """Run one tool safely. Errors go back to the model as text it can act on."""
        self.on_event(f"  -> {name}({arguments[:80]})")
        tool = self.tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'. Available: {', '.join(self.tools)}."
        try:
            args = json.loads(arguments or "{}")
            return str(tool(**args))
        except json.JSONDecodeError:
            return "Error: arguments were not valid JSON."
        except TypeError as exc:
            return f"Error: wrong arguments for {name}: {exc}"
        except ToolError as exc:
            return f"Error: {exc}"


def make_client() -> tuple[OpenAI, str]:
    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    key, model = os.getenv("LLM_API_KEY"), os.getenv("LLM_MODEL")
    if not key or not model:
        raise SystemExit("Set LLM_API_KEY and LLM_MODEL (and LLM_BASE_URL for non-OpenAI providers).")
    return OpenAI(base_url=base_url, api_key=key), model
