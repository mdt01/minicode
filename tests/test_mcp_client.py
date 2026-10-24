"""v0.6: MCP tools reach the model, and calls go to the server (in-process, no network)."""
import json

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from minicode.agent import Agent
from minicode.mcp_client import MCPConnection, target_from_config
from minicode.tools import Workspace
from tests.test_agent_loop import ScriptedClient, tool_call

demo = MCPServer("demo")


@demo.tool()
def add(a: int, b: int) -> str:
    """Add two integers."""
    return str(a + b)


@demo.tool()
def fail(reason: str) -> str:
    """Always fails."""
    raise ToolError(f"cannot do that: {reason}")


def test_connection_lists_and_calls_tools():
    connection = MCPConnection("demo", demo)
    tools = connection.agent_tools()
    assert sorted(tools) == ["mcp__demo__add", "mcp__demo__fail"]
    function, schema = tools["mcp__demo__add"]
    assert schema["function"]["parameters"]["required"] == ["a", "b"]
    assert function(a=2, b=3) == "5"
    assert tools["mcp__demo__fail"][0](reason="x").startswith("Error:")
    connection.close()


def test_agent_offers_and_executes_mcp_tools(tmp_path):
    connection = MCPConnection("demo", demo)
    client = ScriptedClient([tool_call("c1", "mcp__demo__add", {"a": 40, "b": 2}),
                             {"content": "The answer is 42."}])
    agent = Agent(client, "fake", Workspace(tmp_path), on_event=lambda _: None)
    agent.add_tools(connection.agent_tools())
    assert agent.run_turn("add 40 and 2") == "The answer is 42."
    names = [t["function"]["name"] for t in client.sent[0]["tools"]]
    assert "mcp__demo__add" in names and "read_file" in names
    assert agent.messages[3]["content"] == "42"
    agent.mode = "plan"                                   # MCP tools are off in plan mode
    assert "mcp__demo__add" not in agent.active_tools()
    connection.close()


def test_config_entries_become_targets(tmp_path):
    assert target_from_config({"url": "http://x/mcp"}, tmp_path) == "http://x/mcp"
    stdio = target_from_config({"command": "python", "args": ["s.py"]}, tmp_path)
    assert stdio.command == "python" and stdio.args == ["s.py"] and stdio.cwd == str(tmp_path)
