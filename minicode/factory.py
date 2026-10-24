"""minicode v1.0: a mini software factory - a spec goes in, a verified, previewed change comes out.

    python -m minicode.factory run specs/registration-window.toml --repo ../campushub
    python -m minicode.factory run specs/x.toml --pause-after plan     # human checkpoint
    python -m minicode.factory approve registration-window-20261024-0912
    python -m minicode.factory status registration-window-20261024-0912

The pipeline (each stage is checkpointed in .minicode/factory/<run>.json):

  plan       a read-only planner agent turns the spec into a plan      [optional pause]
  implement  a maker agent changes the code in its own worktree
  verify     the spec's verify commands, a security scan of the changed files, and a
             read-only checker agent; failures go back to implement (bounded)
  publish    commit on a factory branch (and, with --open-pr, a draft pull request)
  preview    start the app on a free port with a throw-away database, run the spec's
             smoke checks over HTTP, watch the server log for errors, stop the app

minicode is one worker in the factory: the same Agent, tools, permissions, hooks and
gates as in the rest of the book, driven by a spec instead of a person.
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from minicode.background import BudgetClient, BudgetExceeded, git
from minicode.security import clean_env

STAGES = ["plan", "implement", "verify", "publish", "preview"]
STATE_DIR = Path(".minicode") / "factory"

PLANNER_PROMPT = """You are the PLANNER in a software factory. Read the code the spec
concerns, then write an implementation plan: files to change (with functions), the tests
to add (one per acceptance criterion), and risks. Do not change any file. Reply with the
plan only, under 300 words.

{spec}"""

MAKER_PROMPT = """You are the MAKER in a software factory. Implement the spec below by
following the approved plan. Add a test for every acceptance criterion. Never weaken or
delete existing tests. Stay inside the spec's scope. Nobody can answer questions.

{spec}

## Approved plan
{plan}
{feedback}"""

CHECKER_PROMPT = """You are the CHECKER in a software factory. You did not write this change.
All verify commands passed. Decide whether the change implements the spec honestly:
every acceptance criterion covered by code and a test, nothing out of scope, no weakened
tests, no obvious bugs or security problems. Reply with APPROVE on the first line, or
REJECT: followed by the reasons.

{spec}

## The diff
{diff}"""


@dataclass
class Spec:
    name: str
    title: str
    goal: str
    acceptance: list[str]
    context: str = ""
    out_of_scope: list[str] = field(default_factory=list)
    verify: list[str] = field(default_factory=list)
    app: str = ""                          # e.g. "app.main:app" for the preview
    health: str = "/health"
    env: dict = field(default_factory=dict)
    smoke: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Spec":
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        data.setdefault("name", path.stem)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def as_text(self) -> str:
        items = "\n".join(f"{i}. {a}" for i, a in enumerate(self.acceptance, 1))
        out = "\n".join(f"- {o}" for o in self.out_of_scope) or "- (none listed)"
        return (f"# Spec: {self.title}\n\n## Goal\n{self.goal}\n\n## Context\n"
                f"{self.context or '-'}\n\n## Acceptance criteria\n{items}\n\n"
                f"## Out of scope\n{out}\n\n## Verify\n" + "\n".join(self.verify))


class Factory:
    def __init__(self, repo: Path, spec: Spec, make_agent, client, python: str = sys.executable,
                 open_pr: bool = False, pause_after: str | None = None,
                 max_attempts: int = 2, budget: int = 600_000, run_id: str | None = None,
                 on_event=print):
        self.repo, self.spec, self.make_agent = repo.resolve(), spec, make_agent
        self.python, self.open_pr, self.pause_after = python, open_pr, pause_after
        self.max_attempts, self.on_event = max_attempts, on_event
        self.run_id = run_id or f"{spec.name}-{time.strftime('%Y%m%d-%H%M%S')}"
        self.dir = self.repo / STATE_DIR
        self.state = self.load()
        self.client = BudgetClient(client, budget, self.state["tokens"])

    # ---- state ---------------------------------------------------------------------
    def load(self) -> dict:
        file = self.dir / f"{self.run_id}.json"
        if file.is_file():
            return json.loads(file.read_text(encoding="utf-8"))
        return {"run": self.run_id, "spec": self.spec.name, "status": "running",
                "stage": "plan", "attempt": 0, "tokens": 0, "plan": "", "feedback": "",
                "branch": f"factory/{self.run_id}", "base": git(self.repo, "rev-parse", "HEAD"),
                "results": {}, "events": []}

    def save(self) -> None:
        self.state["tokens"] = getattr(self, "client", None) and self.client.used or 0
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f"{self.run_id}.tmp"
        tmp.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        os.replace(tmp, self.dir / f"{self.run_id}.json")

    def event(self, text: str) -> None:
        self.state["events"].append(f"{time.strftime('%H:%M:%S')} {text}")
        self.on_event(f"[factory] {text}")

    @property
    def worktree(self) -> Path:
        return self.repo.parent / f"{self.repo.name}-factory" / self.run_id

    # ---- the pipeline --------------------------------------------------------------
    def run(self) -> dict:
        s = self.state
        if s["status"] in ("done", "failed"):
            return s
        s["status"] = "running"
        if not self.worktree.exists():
            self.worktree.parent.mkdir(parents=True, exist_ok=True)
            git(self.repo, "worktree", "add", "-b", s["branch"], str(self.worktree), s["base"])
        try:
            while s["stage"] != "end":
                stage = s["stage"]
                self.event(f"stage {stage}")
                next_stage = getattr(self, f"stage_{stage}")()
                s["stage"] = next_stage
                self.save()                               # checkpoint after every stage
                if s["status"] in ("awaiting-approval", "failed"):
                    return s
            s["status"] = "done"
        except BudgetExceeded as exc:
            s["status"], s["error"] = "failed", str(exc)
        self.write_report()
        self.save()
        return s

    def stage_plan(self) -> str:
        planner = self.make_agent(self.worktree, "planner", self.client)
        self.state["plan"] = planner.run_turn(PLANNER_PROMPT.format(spec=self.spec.as_text()))
        (self.dir / f"{self.run_id}-plan.md").parent.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{self.run_id}-plan.md").write_text(self.state["plan"], encoding="utf-8")
        self.state["results"]["plan"] = "written"
        if self.pause_after == "plan":
            self.state["status"] = "awaiting-approval"
            self.event("paused: a human must approve the plan (factory approve <run>)")
        return "implement"

    def stage_implement(self) -> str:
        s = self.state
        s["attempt"] += 1
        feedback = (f"\n## Feedback from the previous attempt\n{s['feedback']}"
                    if s["feedback"] else "")
        maker = self.make_agent(self.worktree, "maker", self.client)
        summary = maker.run_turn(MAKER_PROMPT.format(spec=self.spec.as_text(),
                                                     plan=s["plan"], feedback=feedback))
        s["results"]["implement"] = f"attempt {s['attempt']}: {summary[:300]}"
        return "verify"

    def stage_verify(self) -> str:
        s, problems = self.state, []
        for command in self.spec.verify:
            ok, out = self.shell(command.format(python=self.python))
            s["results"][f"verify: {command}"] = "pass" if ok else "FAIL"
            if not ok:
                problems.append(f"`{command}` failed:\n{out}")
        changed = [f for f in git(self.worktree, "diff", "--name-only", s["base"]).split()
                   if f.endswith(".py") and not f.startswith("tests/")]
        if changed:                                       # scan only what changed
            ok, out = self.shell(f"{self.python} -m bandit -q " + " ".join(changed))
            s["results"]["security scan (changed files)"] = "pass" if ok else "FAIL"
            if not ok:
                problems.append(f"Security scan of changed files:\n{out}")
        if not problems:
            diff = git(self.worktree, "diff", s["base"])[:20000] or "(no changes)"
            checker = self.make_agent(self.worktree, "checker", self.client)
            verdict = checker.run_turn(CHECKER_PROMPT.format(spec=self.spec.as_text(),
                                                             diff=diff)).strip()
            s["results"]["checker"] = verdict.splitlines()[0][:120] if verdict else "-"
            if verdict.upper().startswith("APPROVE"):
                s["feedback"] = ""
                return "publish"
            problems.append("The checker rejected the change:\n" + verdict)
        s["feedback"] = "\n\n".join(problems)
        if s["attempt"] >= self.max_attempts:
            s["status"], s["error"] = "failed", f"verification failed {s['attempt']} times"
            self.event("escalated to a human: " + s["error"])
            self.write_report()
            return "verify"
        return "implement"

    def stage_publish(self) -> str:
        s = self.state
        git(self.worktree, "add", "-A")
        if git(self.worktree, "status", "--porcelain"):
            git(self.worktree, "-c", "user.name=minicode-factory",
                "-c", "user.email=factory@localhost", "commit", "-q", "-m",
                f"[factory] {self.spec.title}\n\nSpec: {self.spec.name}, run {self.run_id}.")
        s["results"]["publish"] = f"commit {git(self.worktree, 'rev-parse', '--short', 'HEAD')}"
        if self.open_pr:
            git(self.worktree, "push", "-u", "origin", s["branch"])
            done = subprocess.run(["gh", "pr", "create", "--draft", "--head", s["branch"],
                                   "--title", f"[factory] {self.spec.title}",
                                   "--body", self.report_text()], cwd=self.worktree,
                                  capture_output=True, text=True)
            s["results"]["pull request"] = done.stdout.strip() or done.stderr.strip()
        return "preview" if self.spec.app else "end"

    def stage_preview(self) -> str:
        s = self.state
        port, tmp = free_port(), tempfile.mkdtemp(prefix="factory-")
        env = clean_env() | {k: v.format(tmp=tmp) for k, v in self.spec.env.items()}
        log_file = Path(tmp) / "server.log"
        with log_file.open("w") as log:
            server = subprocess.Popen([self.python, "-m", "uvicorn", self.spec.app,
                                       "--port", str(port)], cwd=self.worktree, env=env,
                                      stdout=log, stderr=subprocess.STDOUT)
        base = f"http://127.0.0.1:{port}"
        try:
            if not wait_until_up(base + self.spec.health):
                s["results"]["preview"] = "FAIL: the app did not start"
                s["status"], s["error"] = "failed", "preview deploy failed"
                return "preview"
            s["results"]["preview"] = f"up on port {port}"
            ok = run_smoke(base, self.spec.smoke, s["results"])
        finally:
            server.terminate()
            server.wait(timeout=10)
        errors = [ln for ln in log_file.read_text().splitlines()
                  if re.search(r"Traceback|ERROR|Exception", ln)]
        s["results"]["log watcher"] = "clean" if not errors else f"{len(errors)} error lines"
        if not ok or errors:
            s["status"], s["error"] = "failed", "smoke checks or log watcher failed"
            return "preview"
        return "end"

    # ---- helpers -------------------------------------------------------------------
    def shell(self, command: str) -> tuple[bool, str]:
        done = subprocess.run(command, shell=True, cwd=self.worktree, text=True,
                              capture_output=True, timeout=900, env=clean_env())
        return done.returncode == 0, "\n".join((done.stdout + done.stderr)
                                               .strip().splitlines()[-25:])

    def report_text(self) -> str:
        rows = "\n".join(f"| {k} | {v} |" for k, v in self.state["results"].items())
        return (f"# Factory run {self.run_id}\n\nSpec: {self.spec.title}\n\n"
                f"Status: {self.state['status']}  |  attempts: {self.state['attempt']}  |  "
                f"tokens: {self.client.used:,}\n\n| Stage | Result |\n|---|---|\n{rows}\n\n"
                "AI-generated by a software factory: a human reviews before merging.\n")

    def write_report(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{self.run_id}-report.md").write_text(self.report_text(), encoding="utf-8")


# ---- preview helpers ------------------------------------------------------------------
def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until_up(url: str, seconds: float = 30) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    return False


def fill(value, saved: dict):
    """Templates in smoke checks: {now+30m}, {now-2h}, {name.field} from earlier steps."""
    if isinstance(value, dict):
        return {k: fill(v, saved) for k, v in value.items()}
    if not isinstance(value, str):
        return value

    def replace(match: re.Match) -> str:
        expr = match.group(1)
        offset = re.fullmatch(r"now([+-])(\d+)([mhd])", expr)
        if offset:
            unit = {"m": "minutes", "h": "hours", "d": "days"}[offset.group(3)]
            delta = timedelta(**{unit: int(offset.group(2))})
            moment = datetime.now() + (delta if offset.group(1) == "+" else -delta)
            return moment.replace(microsecond=0).isoformat()
        name, _, key = expr.partition(".")
        return str(saved[name][key])
    return re.sub(r"\{([^{}]+)\}", replace, value)


def run_smoke(base: str, checks: list[dict], results: dict) -> bool:
    saved, all_ok = {}, True
    for number, check in enumerate(checks, 1):
        url = base + fill(check["path"], saved)
        response = httpx.request(check.get("method", "GET"), url,
                                 json=fill(check.get("json"), saved), timeout=10)
        ok = response.status_code == check.get("expect_status", 200)
        if ok and check.get("expect_contains"):
            ok = check["expect_contains"] in response.text
        if check.get("name") and response.headers.get("content-type", "").startswith(
                "application/json"):
            saved[check["name"]] = response.json()
        results[f"smoke {number}: {check.get('method', 'GET')} {check['path']}"] = (
            "pass" if ok else f"FAIL ({response.status_code}: {response.text[:80]})")
        all_ok &= ok
    return all_ok


# ---- the workers ----------------------------------------------------------------------
def default_factory(args):
    from minicode.agent import Agent, make_client
    from minicode.hooks import Hooks
    from minicode.security import Policy, Sandbox
    from minicode.skills import SkillLibrary
    from minicode.tools import Workspace
    from minicode.verify import VerificationGate

    client, model = make_client("factory")

    def make(path: Path, role: str, budget_client):
        workspace = Workspace(path, confirm=lambda _: False)
        workspace.sandbox = Sandbox(args.sandbox, args.sandbox_image)
        agent = Agent(budget_client, model, workspace, max_steps=args.max_steps,
                      on_event=lambda e: print(f"    [{role}] {e.strip()[:100]}"))
        agent.add_skills(SkillLibrary(path))
        if role == "maker":
            agent.add_hooks(Hooks(path))
            agent.policy = Policy(path, args.mode)
            agent.gate = VerificationGate(path, sandbox=workspace.sandbox)
        else:                                             # planner and checker only read
            agent.policy = Policy(path, "read-only")
        return agent
    return make, client


def main() -> None:
    parser = argparse.ArgumentParser(prog="minicode.factory")
    parser.add_argument("--repo", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("spec")
    run.add_argument("--python", default=sys.executable,
                     help="the project's Python (runs tests, scans and the preview)")
    run.add_argument("--pause-after", choices=["plan"], help="stop for human approval")
    run.add_argument("--open-pr", action="store_true")
    run.add_argument("--max-attempts", type=int, default=2)
    run.add_argument("--budget", type=int, default=600_000)
    run.add_argument("--mode", default="ask")
    run.add_argument("--sandbox", choices=["none", "docker"], default="none")
    run.add_argument("--sandbox-image", default="python:3.12-slim")
    run.add_argument("--max-steps", type=int, default=40)
    for name in ("approve", "status"):
        p = sub.add_parser(name)
        p.add_argument("run_id")
        p.add_argument("--python", default=sys.executable)
        for flag, kw in (("--open-pr", {"action": "store_true"}),
                         ("--max-steps", {"type": int, "default": 40}),
                         ("--mode", {"default": "ask"}),
                         ("--sandbox", {"default": "none"}),
                         ("--sandbox-image", {"default": "python:3.12-slim"})):
            p.add_argument(flag, **kw)
    args = parser.parse_args()
    repo = Path(args.repo).resolve()

    if args.command == "status":
        file = repo / STATE_DIR / f"{args.run_id}.json"
        print(file.read_text(encoding="utf-8") if file.is_file() else "unknown run")
        return
    if args.command == "run":
        spec_path, run_id = repo / args.spec, None
    else:                                                 # approve a paused run
        state = json.loads((repo / STATE_DIR / f"{args.run_id}.json").read_text())
        spec_path, run_id = repo / "specs" / f"{state['spec']}.toml", args.run_id
    make, client = default_factory(args)
    factory = Factory(repo, Spec.load(spec_path), make, client, args.python,
                      args.open_pr, getattr(args, "pause_after", None),
                      getattr(args, "max_attempts", 2), getattr(args, "budget", 600_000),
                      run_id)
    state = factory.run()
    print(factory.report_text())
    raise SystemExit(0 if state["status"] in ("done", "awaiting-approval") else 3)


if __name__ == "__main__":
    main()
