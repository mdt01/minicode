"""v0.9: nested agent files, hooks and subagents."""
import json
import sys

from minicode.agent import Agent
from minicode.hooks import Hooks
from minicode.subagents import load_definitions
from minicode.tools import Workspace
from tests.test_agent_loop import ScriptedClient, tool_call

PY = sys.executable.replace("\\", "/")


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_nested_agent_file_loads_when_its_folder_is_touched(tmp_path):
    write(tmp_path / "AGENTS.md", "root rules")
    write(tmp_path / "app" / "AGENTS.md", "Routers stay thin.")
    write(tmp_path / "app" / "x.py", "x = 1\n")
    client = ScriptedClient([tool_call("c1", "read_file", {"path": "app/x.py"}),
                             tool_call("c2", "read_file", {"path": "app/x.py"}),
                             {"content": "done"}])
    agent = Agent(client, "fake", Workspace(tmp_path), on_event=lambda _: None)
    agent.run_turn("look at app/x.py")
    assert "Routers stay thin." in agent.messages[3]["content"]       # first read
    assert "Routers stay thin." not in agent.messages[5]["content"]   # only once


def test_pre_tool_hook_blocks_protected_files(tmp_path):
    write(tmp_path / ".agents" / "guard.py",
          "import json, sys\n"
          "e = json.load(sys.stdin)\n"
          "if e['arguments'].get('path', '').endswith('.env'):\n"
          "    print('Never edit .env files; ask the user.', file=sys.stderr); sys.exit(2)\n")
    write(tmp_path / ".agents" / "hooks.json", json.dumps({"pre_tool": [
        {"match": "edit_file", "command": f'"{PY}" .agents/guard.py'}]}))
    client = ScriptedClient([
        tool_call("c1", "edit_file", {"path": ".env", "old_text": "", "new_text": "K=1"}),
        {"content": "ok"}])
    agent = Agent(client, "fake", Workspace(tmp_path), on_event=lambda _: None)
    agent.add_hooks(Hooks(tmp_path))
    agent.run_turn("add a key to .env")
    assert agent.messages[3]["content"].startswith("Error: blocked by a project hook")
    assert "Never edit .env" in agent.messages[3]["content"]
    assert not (tmp_path / ".env").exists()


def test_stop_hook_sends_the_agent_back_to_work(tmp_path):
    write(tmp_path / ".agents" / "gate.py",
          "import json, os, sys\n"
          "e = json.load(sys.stdin)\n"
          "if e['changed_files'] and not os.path.exists('fixed'):\n"
          "    print('Tests fail: 1 failed', file=sys.stderr); sys.exit(2)\n")
    write(tmp_path / ".agents" / "hooks.json", json.dumps({"stop": [
        {"command": f'"{PY}" .agents/gate.py'}]}))
    client = ScriptedClient([
        tool_call("c1", "edit_file", {"path": "a.py", "old_text": "", "new_text": "x"}),
        {"content": "Done!"},                                         # refused by the gate
        tool_call("c2", "edit_file", {"path": "fixed", "old_text": "", "new_text": "y"}),
        {"content": "Done, tests pass."}])
    agent = Agent(client, "fake", Workspace(tmp_path), on_event=lambda _: None)
    agent.add_hooks(Hooks(tmp_path))
    assert agent.run_turn("change a.py") == "Done, tests pass."
    assert any("Tests fail: 1 failed" in str(m.get("content")) for m in agent.messages)


def test_subagent_gets_fresh_context_and_limited_tools(tmp_path):
    write(tmp_path / ".agents" / "agents" / "reviewer.md",
          "---\nname: reviewer\ndescription: Reviews diffs.\ntools: read_file, list_files\n"
          "---\nYou are a strict reviewer.")
    client = ScriptedClient([
        tool_call("c1", "delegate", {"agent": "reviewer", "task": "Review app.py"}),
        {"content": "LGTM, one nit."},                                # the subagent
        {"content": "The reviewer approved with one nit."}])          # back in the parent
    agent = Agent(client, "fake", Workspace(tmp_path), on_event=lambda _: None)
    agent.add_subagents(load_definitions(tmp_path))
    assert agent.run_turn("review my change") == "The reviewer approved with one nit."
    child_request = client.sent[1]
    assert child_request["messages"][1]["content"].endswith("Review app.py")
    assert not any("review my change" in str(m.get("content"))      # fresh context
                   for m in child_request["messages"])
    assert "strict reviewer" in child_request["messages"][0]["content"]
    assert sorted(t["function"]["name"] for t in child_request["tools"]) == \
        ["list_files", "read_file"]
    assert agent.messages[3]["content"] == "[reviewer finished]\nLGTM, one nit."
    assert agent.tokens["input"] == 300                               # parent + child
