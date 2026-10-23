"""Run the agent loop against a scripted fake model (no network, no API key)."""
import json

from openai.types.chat import ChatCompletion

from minicode.agent import Agent
from minicode.tools import Workspace


def completion(message: dict) -> ChatCompletion:
    return ChatCompletion.model_validate({
        "id": "x", "object": "chat.completion", "created": 0, "model": "fake",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", **message}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    })


class ScriptedClient:
    """Replays a fixed list of model replies and records what it was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.sent.append(kwargs)
        return completion(self.replies.pop(0))


def tool_call(call_id, name, args):
    return {"content": None, "tool_calls": [{"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}]}


def test_agent_reads_edits_and_finishes(tmp_path):
    (tmp_path / "hello.py").write_text('print("helo")\n')
    client = ScriptedClient([
        tool_call("c1", "read_file", {"path": "hello.py"}),
        tool_call("c2", "edit_file", {"path": "hello.py", "old_text": "helo", "new_text": "hello"}),
        {"content": "Fixed the typo in hello.py."},
    ])
    agent = Agent(client, "fake", Workspace(tmp_path), on_event=lambda _: None)
    answer = agent.run_turn("Fix the typo in hello.py")
    assert answer == "Fixed the typo in hello.py."
    assert (tmp_path / "hello.py").read_text() == 'print("hello")\n'
    assert agent.tokens == {"input": 300, "output": 60}
    assert [m["role"] for m in agent.messages] == ["system", "user", "assistant", "tool", "assistant", "tool", "assistant"]


def test_tool_errors_are_returned_to_the_model(tmp_path):
    client = ScriptedClient([tool_call("c1", "read_file", {"path": "missing.py"}), {"content": "It does not exist."}])
    agent = Agent(client, "fake", Workspace(tmp_path), on_event=lambda _: None)
    agent.run_turn("Read missing.py")
    assert agent.messages[3]["content"].startswith("Error: No file at 'missing.py'")


def test_step_limit_stops_runaway_loops(tmp_path):
    client = ScriptedClient([tool_call(f"c{i}", "list_files", {}) for i in range(3)])
    agent = Agent(client, "fake", Workspace(tmp_path), max_steps=3, on_event=lambda _: None)
    assert agent.run_turn("loop forever").startswith("Stopped")
