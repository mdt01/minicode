"""Tests for permission modes, rules, sandboxing and the audit log (Chapter 14)."""
import json

from minicode.agent import Agent
from minicode.security import Policy, Sandbox, clean_env, wrap_untrusted
from minicode.tools import Workspace
from tests.test_agent_loop import ScriptedClient, tool_call

RULES = {"allow": ["run_bash(python -m pytest*)"], "ask": ["run_bash(pip install*)"],
         "deny": ["run_bash(git push*)", "edit_file(migrations/*)"]}


def test_rules_and_modes(tmp_path):
    policy = Policy(tmp_path, "ask", RULES)
    assert policy.decide("read_file", {"path": ".env"})[0] == "deny"          # built-in
    assert policy.decide("read_file", {"path": ".env.example"})[0] == "allow"
    assert policy.decide("run_bash", {"command": "python -m pytest -q"})[0] == "allow"
    assert policy.decide("run_bash", {"command": "pip install requests"})[0] == "ask"
    assert policy.decide("run_bash", {"command": "ls"})[0] == "default"
    assert policy.decide("edit_file", {"path": "migrations/001.py"})[0] == "deny"
    auto = Policy(tmp_path, "auto", RULES)
    assert auto.decide("run_bash", {"command": "git push origin main"})[0] == "deny"
    assert auto.decide("run_bash", {"command": "rm -rf build"})[0] == "allow"
    read_only = Policy(tmp_path, "read-only", RULES)
    assert read_only.decide("edit_file", {"path": "app.py"})[0] == "deny"
    assert read_only.decide("list_files", {})[0] == "allow"


def test_rules_file_is_loaded(tmp_path):
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "permissions.json").write_text(json.dumps(RULES))
    assert Policy(tmp_path).decide("run_bash", {"command": "git push"})[0] == "deny"


def test_denied_calls_are_refused_and_logged(tmp_path):
    (tmp_path / ".env").write_text("SECRET_KEY=not-for-the-model\n")
    client = ScriptedClient([
        tool_call("c1", "list_files", {}),
        tool_call("c2", "read_file", {"path": ".env"}),
        {"content": "The settings file is protected; please share the value I need."}])
    agent = Agent(client, "fake", Workspace(tmp_path, confirm=lambda _: True),
                  on_event=lambda _: None)
    agent.policy = Policy(tmp_path, "auto", RULES)
    agent.run_turn("Check the configuration")
    results = [m["content"] for m in agent.messages if m["role"] == "tool"]
    assert "not-for-the-model" not in "".join(results)
    assert results[1].startswith("Error: denied by the permission policy")
    log = (tmp_path / ".minicode" / "audit.jsonl").read_text().splitlines()
    assert [json.loads(line)["decision"] for line in log] == ["allow", "deny"]


def test_ask_decision_uses_confirmation(tmp_path):
    asked = []
    client = ScriptedClient([tool_call("c1", "run_bash", {"command": "pip install x"}),
                             {"content": "ok"}])
    agent = Agent(client, "fake", Workspace(tmp_path, confirm=lambda c: asked.append(c)),
                  on_event=lambda _: None)
    agent.policy = Policy(tmp_path, "ask", RULES)
    agent.run_turn("install x")
    assert asked == ["run_bash: pip install x"]
    assert "declined" in agent.messages[3]["content"]


def test_clean_env_and_sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-test")
    env = clean_env()
    assert "LLM_API_KEY" not in env and "GITHUB_TOKEN" not in env and "PATH" in env
    argv = Sandbox("docker", "campushub-sandbox").argv("pytest -q", tmp_path)
    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[-3:] == ["sh", "-c", "pytest -q"]
    out = Sandbox().run("echo hello", tmp_path)
    assert out.stdout.strip() == "hello"
    assert "untrusted-data" in wrap_untrusted("mcp__web__fetch", "some page text")


def test_git_config_group_is_dropped_together(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "http.extraheader")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "Authorization: Bearer x")
    env = clean_env()
    assert not [k for k in env if k.startswith("GIT_CONFIG_")]
