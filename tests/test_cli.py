"""The interactive session survives a failed model call (found in Lab 5: a network blip
during a long session crashed minicode and lost the conversation)."""
import builtins

import httpx
from openai import APIConnectionError

from minicode import __main__ as cli


class FlakyClient:
    """Fails the first call with a connection error, then answers normally."""

    def __init__(self):
        self.calls = 0
        self.chat = self
        self.completions = self

    def create(self, **request):
        self.calls += 1
        if self.calls == 1:
            raise APIConnectionError(request=httpx.Request("POST", "http://test"))
        from types import SimpleNamespace as NS
        message = NS(content="hello again", tool_calls=None, role="assistant",
                     model_dump=lambda **_: {"role": "assistant", "content": "hello again"})
        return NS(choices=[NS(message=message, finish_reason="stop")],
                  usage=NS(prompt_tokens=10, completion_tokens=2))


def test_session_survives_a_failed_model_call(tmp_path, monkeypatch, capsys):
    client = FlakyClient()
    monkeypatch.setattr(cli, "make_client", lambda: (client, "fake"))
    monkeypatch.setattr("sys.argv", ["minicode", "--workspace", str(tmp_path)])
    lines = iter(["first question", "second question", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(lines))
    cli.main()
    out = capsys.readouterr().out
    assert "model call failed (APIConnectionError" in out
    assert "minicode> hello again" in out          # the session went on
    assert client.calls == 2


def test_max_steps_option_reaches_the_agent(tmp_path, monkeypatch):
    created = {}
    real_agent = cli.Agent

    def spy(*args, **kwargs):
        agent = real_agent(*args, **kwargs)
        created["agent"] = agent
        return agent

    monkeypatch.setattr(cli, "Agent", spy)
    monkeypatch.setattr(cli, "make_client", lambda: (FlakyClient(), "fake"))
    monkeypatch.setattr("sys.argv", ["minicode", "--workspace", str(tmp_path), "--max-steps", "40"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": "/exit")
    cli.main()
    assert created["agent"].max_steps == 40
