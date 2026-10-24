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

    def fake_request(method, url, json=None, timeout=10):
        calls.append((method, url, json))
        if url.endswith("/events"):
            return httpx.Response(201, json={"id": 42})
        return httpx.Response(400, json={"detail": "Registration closed: ..."})
    monkeypatch.setattr(factory_module.httpx, "request", fake_request)
    results = {}
    ok = run_smoke("http://x", [
        {"name": "ev", "method": "POST", "path": "/events", "json": {"t": "a"},
         "expect_status": 201},
        {"method": "POST", "path": "/events/{ev.id}/register", "expect_status": 400,
         "expect_contains": "Registration closed"}], results)
    assert ok and calls[1][1] == "http://x/events/42/register"
    assert list(results.values()) == ["pass", "pass"]
