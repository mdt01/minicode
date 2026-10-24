"""Tests for background mode: queue, worktrees, durable steps, budgets (Chapter 15)."""
from minicode.agent import Agent
from minicode.background import BudgetClient, TaskStore, Worker, git
from minicode.tools import Workspace
from tests.test_agent_loop import ScriptedClient, tool_call


def make_repo(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@x"],
                 ["config", "user.name", "t"]):
        git(repo, *args)
    (repo / "app.py").write_text("x = 1\n")
    (repo / ".gitignore").write_text(".minicode/\n")
    git(repo, "add", "."), git(repo, "commit", "-qm", "base")
    return repo


def factory(replies, budget_override=None):
    client = ScriptedClient(replies)

    def make(path, task, on_event):
        budget = budget_override or task["budget"]
        return Agent(BudgetClient(client, budget, task["tokens"]), "fake",
                     Workspace(path, confirm=lambda _: False), on_event=on_event)
    return make


EDIT = [tool_call("c1", "edit_file", {"path": "app.py", "old_text": "x = 1",
                                      "new_text": "x = 2"}),
        {"content": "Changed x to 2; nothing else to verify."}]


def test_task_runs_in_its_own_worktree_and_branch(tmp_path):
    repo = make_repo(tmp_path)
    store = TaskStore(repo)
    task = store.add("Set x to 2 in app.py")
    done = Worker(store, factory(EDIT)).run_task(task)
    assert done["status"] == "done" and done["done_steps"] == ["worktree", "agent", "commit"]
    assert (repo / "app.py").read_text() == "x = 1\n"            # your copy untouched
    assert git(repo, "show", f"{task['branch']}:app.py") == "x = 2"
    assert done["tokens"] == 240
    log = (store.dir / f"{task['id']}.log.jsonl").read_text()
    assert "edit_file" in log and "commit done" in log


def test_interrupted_task_resumes_without_repeating_finished_steps(tmp_path):
    repo = make_repo(tmp_path)
    store = TaskStore(repo)
    task = store.add("Set x to 2")
    worker = Worker(store, factory(EDIT))
    worker.step_worktree(task)                         # the first attempt got this far...
    task["done_steps"], task["status"] = ["worktree"], "running"
    store.save(task)                                   # ...and then the worker crashed
    assert [t["id"] for t in worker.pending()] == [task["id"]]
    done = worker.run_task(store.load(task["id"]))
    assert done["status"] == "done"
    log = (store.dir / f"{task['id']}.log.jsonl").read_text()
    assert "worktree already done" in log


def test_budget_stops_the_task(tmp_path):
    repo = make_repo(tmp_path)
    store = TaskStore(repo)
    task = store.add("Set x to 2", budget=100)
    endless = [tool_call(f"c{i}", "list_files", {}) for i in range(10)]
    done = Worker(store, factory(endless)).run_task(task)
    assert done["status"] == "failed" and "budget" in done["error"]


def test_no_changes_and_cancellation(tmp_path):
    repo = make_repo(tmp_path)
    store = TaskStore(repo)
    quiet = store.add("Explain app.py")
    done = Worker(store, factory([{"content": "It sets x to 1."}])).run_task(quiet)
    assert done["status"] == "no-changes"
    task = store.add("Set x to 2")

    def cancelling(path, t, on_event):
        stored = store.load(t["id"])
        stored["status"] = "cancel-requested"
        store.save(stored)                             # someone runs `cancel` meanwhile
        return factory(EDIT)(path, t, on_event)
    done = Worker(store, cancelling).run_task(task)
    assert done["status"] == "cancelled"
