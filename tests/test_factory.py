"""Tests for the mini software factory (Chapter 18), with scripted agents."""
import sys

import httpx

from minicode import factory as factory_module
from minicode.agent import Agent
from minicode.background import git
from minicode.factory import Factory, Spec, fill, run_smoke
from minicode.tools import Workspace
from tests.test_agent_loop import ScriptedClient, tool_call

CHECK = f'"{sys.executable}" -c "import os,sys; sys.exit(0 if os.path.exists(\'feature.txt\') else 1)"'


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


SPEC = Spec(name="feature", title="Add feature.txt", goal="feature.txt exists",
            acceptance=["feature.txt exists"], verify=[CHECK])


def agents(path, role, client):
    return Agent(client, "fake", Workspace(path, confirm=lambda _: False),
                 on_event=lambda _: None)


def build(repo, replies, **kw):
    return Factory(repo, SPEC, agents, ScriptedClient(replies), run_id="r1",
                   on_event=lambda _: None, **kw)


MAKE = [tool_call("m", "edit_file", {"path": "feature.txt", "old_text": "",
                                    "new_text": "ok"}), {"content": "Created it."}]


def test_spec_to_commit_with_a_human_checkpoint(tmp_path):
    repo = make_repo(tmp_path)
    first = build(repo, [{"content": "Plan: create feature.txt."}], pause_after="plan")
    assert first.run()["status"] == "awaiting-approval"
    assert (repo / ".minicode" / "factory" / "r1-plan.md").is_file()
    second = build(repo, MAKE + [{"content": "APPROVE"}])        # a human approved
    state = second.run()
    assert state["status"] == "done" and state["results"]["checker"] == "APPROVE"
    assert git(repo, "show", f"{state['branch']}:feature.txt") == "ok"
    assert "| publish | commit" in second.report_text()


def test_failed_verification_retries_then_escalates(tmp_path):
    repo = make_repo(tmp_path)
    state = build(repo, [{"content": "Plan."}, {"content": "I did nothing."},
                         {"content": "Still nothing."}], max_attempts=2).run()
    assert state["status"] == "failed" and state["attempt"] == 2
    assert "failed 2 times" in state["error"]
    assert (repo / ".minicode" / "factory" / "r1-report.md").is_file()


def test_smoke_templates_and_checks(monkeypatch):
    assert fill("/events/{ev.id}", {"ev": {"id": 7}}) == "/events/7"
    assert fill("{now+30m}", {})[:2] == "20"
    calls = []

    def fake_request(method, url, json=None, headers=None, timeout=10):
        calls.append((method, url, json, headers))
        if url.endswith("/events"):
            return httpx.Response(201, json={"id": 42})
        return httpx.Response(400, json={"detail": "Registration closed: ..."})
    monkeypatch.setattr(factory_module.httpx, "request", fake_request)
    results = {}
    ok = run_smoke("http://x", [
        {"name": "ev", "method": "POST", "path": "/events", "json": {"t": "a"},
         "expect_status": 201},
        {"method": "POST", "path": "/events/{ev.id}/register", "expect_status": 400,
         "headers": {"Authorization": "Bearer {ev.id}"},
         "expect_contains": "Registration closed"}], results)
    assert ok and calls[1][1] == "http://x/events/42/register"
    assert calls[1][3] == {"Authorization": "Bearer 42"} and calls[0][3] is None
    assert list(results.values()) == ["pass", "pass"]


def test_repo_option_works_before_and_after_the_command(tmp_path):
    import subprocess
    for argv in (["--repo", str(tmp_path), "status", "nope"], ["status", "nope", "--repo", str(tmp_path)]):
        done = subprocess.run([sys.executable, "-m", "minicode.factory", *argv],
                              capture_output=True, text=True)
        assert done.returncode == 0 and "unknown run" in done.stdout


NEW_MODULE = [tool_call("m", "edit_file", {"path": "feature.txt", "old_text": "",
                                          "new_text": "ok"}),
              tool_call("n", "edit_file", {"path": "notes.md", "old_text": "",
                                          "new_text": "SECRET = 1\n"}),
              {"content": "Created both."}]


def test_new_files_reach_the_checker_and_the_scope_check(tmp_path):
    repo = make_repo(tmp_path)
    client = ScriptedClient([{"content": "Plan."}] + NEW_MODULE + [{"content": "APPROVE"}])
    state = Factory(repo, SPEC, agents, client, run_id="r1", on_event=lambda _: None).run()
    assert state["status"] == "done"
    checker_prompt = next(m["content"] for m in client.sent[-1]["messages"]
                          if "You are the CHECKER" in str(m.get("content")))
    assert "notes.md" in checker_prompt and "SECRET = 1" in checker_prompt   # untracked before
    assert "scope check" not in state["results"]                  # no allowed_paths: no check

    repo2 = make_repo(tmp_path / "two")
    scoped = Spec(name="feature", title="Add feature.txt", goal="feature.txt exists",
                  acceptance=["feature.txt exists"], verify=[CHECK],
                  allowed_paths=["feature.txt"])
    client = ScriptedClient([{"content": "Plan."}] + NEW_MODULE)
    state = Factory(repo2, scoped, agents, client, run_id="r1", max_attempts=1,
                    on_event=lambda _: None).run()
    assert state["status"] == "failed"
    assert state["results"]["scope check"] == "FAIL: notes.md"
    assert "outside the spec's allowed paths" in state["feedback"]


def edit(call_id, path, old, new):
    return tool_call(call_id, "edit_file", {"path": path, "old_text": old, "new_text": new})


def test_security_scan_blocks_only_issues_the_change_adds(tmp_path, monkeypatch):
    def fake_scan(self, root, files):                    # "finds" every line with eval(
        return [(f, "B307", line.strip(), n, "eval") for f in files
                for n, line in enumerate((root / f).read_text().splitlines(), 1)
                if "eval(" in line]
    monkeypatch.setattr(Factory, "scan", fake_scan)
    repo = make_repo(tmp_path)
    (repo / "app.py").write_text("x = eval('1')\n")       # an old issue, out of scope
    git(repo, "commit", "-qam", "old issue")
    clean = [edit("a", "app.py", "x = eval('1')\n", "x = eval('1')\ny = 2\n")] + MAKE
    state = build(repo, [{"content": "Plan."}] + clean + [{"content": "APPROVE"}]).run()
    assert state["status"] == "done"
    assert state["results"]["security scan (changed files)"] == (
        "pass (1 existing issue(s) in these files left alone)")

    repo2 = make_repo(tmp_path / "two")
    (repo2 / "app.py").write_text("x = eval('1')\n")
    git(repo2, "commit", "-qam", "old issue")
    risky = [edit("a", "app.py", "x = eval('1')\n", "x = eval('1')\ny = eval(input())\n")]
    state = build(repo2, [{"content": "Plan."}] + risky + MAKE, max_attempts=1).run()
    assert state["results"]["security scan (changed files)"] == "FAIL: 1 new issue(s)"
    assert "app.py:2 B307" in state["feedback"] and "x = eval" not in state["feedback"]


def test_real_scanner_compares_with_the_base(tmp_path):
    import importlib.util
    if importlib.util.find_spec("bandit") is None:
        import pytest
        pytest.skip("bandit is not installed")
    repo = make_repo(tmp_path)
    (repo / "app.py").write_text("import subprocess\nsubprocess.call('ls', shell=True)\n")
    git(repo, "commit", "-qam", "old issue")
    factory = build(repo, [])
    factory.worktree.parent.mkdir(parents=True)
    git(repo, "worktree", "add", "-b", "w", str(factory.worktree), "HEAD")
    target = factory.worktree / "app.py"
    target.write_text(target.read_text() + "x = 1\n")
    new, old = factory.new_findings(["app.py"])
    assert new == [] and old >= 2                        # B404, B602 ...: all were there
    target.write_text(target.read_text() + "y = eval(input())\n")
    again, still_old = factory.new_findings(["app.py"])
    assert still_old == old and len(again) == 1 and "B307" in again[0]


def test_every_failure_writes_a_report_with_the_error(tmp_path):
    repo = make_repo(tmp_path)
    state = build(repo, [{"content": "Plan."}] + MAKE, budget=1).run()
    report = (repo / ".minicode" / "factory" / "r1-report.md").read_text()
    assert state["status"] == "failed" and "Error: token budget of 1 used up" in report
    assert "stopped at stage implement" in state["events"][-1]

    repo2 = make_repo(tmp_path / "two")
    failing_setup = Spec(name="feature", title="Add feature.txt", goal="g",
                         acceptance=["feature.txt exists"], verify=[CHECK], app="x:app",
                         setup=[f'"{sys.executable}" -c "import sys; sys.exit(1)"'])
    state = Factory(repo2, failing_setup, agents,
                    ScriptedClient([{"content": "Plan."}] + MAKE + [{"content": "APPROVE"}]),
                    run_id="r1", on_event=lambda _: None).run()
    report = (repo2 / ".minicode" / "factory" / "r1-report.md").read_text()
    assert state["status"] == "failed" and "Error: preview setup failed" in report
    assert "| implement | attempt 1: Created it. |" in report         # one line per row


def test_the_read_only_checker_is_told_which_checks_already_passed(tmp_path):
    """Capstone: a read-only checker tried to re-run the checks, was denied, and rejected a
    correct change twice. It now gets the results instead."""
    repo = make_repo(tmp_path)
    client = ScriptedClient([{"content": "Plan."}] + MAKE + [{"content": "APPROVE"}])
    state = Factory(repo, SPEC, agents, client, run_id="r1", on_event=lambda _: None).run()
    assert state["status"] == "done"
    prompt = next(m["content"] for m in client.sent[-1]["messages"]
                  if "You are the CHECKER" in str(m.get("content")))
    assert "not run commands" in prompt and "- verify: " in prompt and ": pass" in prompt
