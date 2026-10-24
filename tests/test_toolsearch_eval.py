"""v0.7: tool search mode and the evaluation harness."""
import json

from minicode.agent import Agent
from minicode.evaluate import report, run_task, tools_used
from minicode.tools import Workspace
from tests.test_agent_loop import ScriptedClient, tool_call


def fake_tools():
    def upcoming(days: int = 30):
        return f"Open-Mic Night within {days} days"
    schema = {"type": "function", "function": {
        "name": "mcp__campushub__campushub_upcoming_events",
        "description": "List CampusHub events starting in the next days days.",
        "parameters": {"type": "object", "properties": {"days": {"type": "integer"}}}}}
    return {"mcp__campushub__campushub_upcoming_events": (upcoming, schema)}


def test_search_mode_hides_tools_until_searched(tmp_path):
    client = ScriptedClient([
        tool_call("c1", "search_tools", {"query": "upcoming events"}),
        tool_call("c2", "call_tool", {"name": "mcp__campushub__campushub_upcoming_events",
                                      "arguments": json.dumps({"days": 7})}),
        {"content": "Open-Mic Night is on this week."}])
    agent = Agent(client, "fake", Workspace(tmp_path), on_event=lambda _: None)
    agent.add_tools(fake_tools(), deferred=True)
    task = {"name": "week", "prompt": "What's on?", "expect_tools": ["upcoming_events"],
            "expect_answer": ["Open-Mic"]}
    result = run_task(agent, task)
    sent = [t["function"]["name"] for t in client.sent[0]["tools"]]
    assert "search_tools" in sent and "call_tool" in sent
    assert not any(n.startswith("mcp__") for n in sent)          # deferred, not sent
    assert "upcoming_events" in agent.messages[3]["content"]      # search result
    assert agent.messages[5]["content"] == "Open-Mic Night within 7 days"
    assert result == {"task": "week", "success": True, "calls": 2, "errors": 0,
                      "input_tokens": 300}


def test_call_tool_errors_teach(tmp_path):
    agent = Agent(ScriptedClient([]), "fake", Workspace(tmp_path), on_event=lambda _: None)
    agent.add_tools(fake_tools(), deferred=True)
    assert agent.catalog.call_tool("nope").startswith("Error: no tool named 'nope'")
    assert "JSON object" in agent.catalog.call_tool(
        "mcp__campushub__campushub_upcoming_events", "days=7")
    assert agent.catalog.search_tools("weather").startswith("No tools match")


def test_tools_used_and_report():
    messages = [{"role": "assistant", "tool_calls": [
        {"function": {"name": "call_tool", "arguments": '{"name": "x_tool"}'}},
        {"function": {"name": "read_file", "arguments": "{}"}}]}]
    assert tools_used(messages) == ["x_tool", "read_file"]
    text = report([{"task": "a", "success": True, "calls": 2, "errors": 0, "input_tokens": 100},
                   {"task": "b", "success": False, "calls": 4, "errors": 1, "input_tokens": 300}])
    assert "1/2" in text and "NO" in text
