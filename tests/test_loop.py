"""Tests for the goal-loop runner (Chapter 16), with scripted maker and checker."""
import json
import sys

from minicode.agent import Agent
from minicode.background import git
from minicode.loop import LoopConfig, LoopRunner
from minicode.tools import Workspace
from tests.test_agent_loop import ScriptedClient, tool_call

CHECK = f'"{sys.executable}" -c "import os,sys; sys.exit(0 if os.path.exists(\'fixed.txt\') else 1)"'


def make_repo(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir(parents=True)
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@x"],
                 ["config", "user.name", "t"]):
        git(repo, *args)
    (repo / "app.py").write_text("x = 1\n")
    (repo / ".gitignore").write_text(".minicode/\n")
    git(repo, "add", "."), git(repo, "commit", "-qm", "base")
    return repo


def config(**overrides):
    values = dict(name="demo", goal="fixed.txt exists", check=CHECK,
                  maker_prompt="Make the check pass.", max_iterations=3,
                  budget_tokens=100_000)
    values.update(overrides)
    return LoopConfig(**values)


def factory(path, role, client):
    return Agent(client, "fake", Workspace(path, confirm=lambda _: False),
                 on_event=lambda _: None)


def run(repo, replies, **overrides):
    runner = LoopRunner(repo, config(**overrides), factory, ScriptedClient(replies),
                        on_event=lambda _: None)
    return runner, runner.tick()


def create(name):
    return tool_call("c", "edit_file", {"path": name, "old_text": "", "new_text": "ok"})


def test_iterates_until_check_passes_and_checker_approves(tmp_path):
    repo = make_repo(tmp_path)
    runner, state = run(repo, [
        {"content": "I could not find the problem."},          # maker, iteration 1
        create("fixed.txt"), {"content": "Created fixed.txt."}, # maker, iteration 2
        {"content": "APPROVE\nSmall and honest."}])             # checker
    assert state["status"] == "done" and state["iteration"] == 2
    assert [h["check_passed"] for h in state["history"]] == [False, True]
    assert git(repo, "show", f"{state['branch']}:fixed.txt") == "ok"
    saved = json.loads(runner.state_file.read_text())
    assert saved["status"] == "done" and saved["tokens"] == state["tokens"] > 0


def test_checker_rejection_feeds_back_and_escalates_at_the_cap(tmp_path):
    repo = make_repo(tmp_path)
    runner, state = run(repo, [
        create("fixed.txt"), {"content": "Done."},
        {"content": "REJECT: you only created an empty marker file."},
        {"content": "I cannot do better."}, {"content": "REJECT: still only a marker."},
        {"content": "Still nothing."}, {"content": "REJECT: no real fix."}])
    assert state["status"] == "escalated" and "3 iterations" in state["escalation"]
    assert "checker rejected" in runner.maker_prompt()
    assert (repo / ".minicode" / "loops" / "demo-escalation.md").is_file()
    _, again = run(repo, [])                                     # next night: waits
    assert again["status"] == "escalated"


def test_goal_already_met_is_quiet_and_budget_escalates(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "fixed.txt").write_text("ok")
    _, state = run(repo, [])
    assert state["status"] == "goal-met" and state["iteration"] == 0
    other = make_repo(tmp_path / "b")
    endless = [tool_call(f"c{i}", "list_files", {}) for i in range(20)]
    _, state = run(other, endless, budget_tokens=500)
    assert state["status"] == "escalated" and "budget" in state["escalation"]
