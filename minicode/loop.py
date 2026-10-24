"""minicode v0.14: a goal-loop runner - a system that prompts the agent for you.

    python -m minicode.loop run .agents/loops/keep-ci-green.toml --repo ../campushub
    python -m minicode.loop status keep-ci-green --repo ../campushub

A loop definition (TOML) names a goal, a CHECK command that verifies it (exit code 0 =
goal met), a maker prompt, and limits. One `run` is one "tick" of the loop - run it from
a schedule (cron, CI, a background worker):

  1. Check the goal on the main branch. If it holds, record that and stop: quiet is allowed.
  2. Otherwise open (or resume) a worktree on a loop branch.
  3. Maker: an agent works towards the goal, given the check output and earlier attempts.
  4. Check again. If it passes, a checker agent with a fresh context reviews the diff.
  5. Check passed + checker approved -> commit (and open a draft PR). Done.
     Otherwise feed back what failed and iterate - until max_iterations or the token
     budget is reached; then escalate to a human with a written note.

All state lives in .minicode/loops/<name>.json (atomic writes), not in any context window,
so a crashed tick resumes at the right iteration.
"""
import argparse
import json
import os
import subprocess
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from minicode.background import BudgetClient, BudgetExceeded, git
from minicode.security import clean_env

STATE_DIR = Path(".minicode") / "loops"

MAKER_RULES = """<system-reminder>
You are the MAKER in an automated loop. Nobody can answer questions.
- Work towards the goal only. Keep the change as small as possible.
- Never delete, skip or weaken a test to reach the goal; fix the code or the cause.
- If the goal cannot be reached safely, change nothing and explain why.
- Finish with a short summary: what you changed, and why it reaches the goal.
</system-reminder>"""

CHECKER_PROMPT = """You are the CHECKER in an automated loop. You did not write this change.
Goal of the loop: {goal}
The goal's check command passed. Decide whether the change is acceptable anyway:
- Does it reach the goal honestly (no weakened tests, no special-casing, no hacks)?
- Is it the smallest reasonable change, with nothing unrelated?
- Could it break other behaviour?
Reply with APPROVE on the first line, or REJECT: followed by the reasons.

The diff:
{diff}"""


@dataclass
class LoopConfig:
    name: str
    goal: str
    check: str
    maker_prompt: str
    discover: str = ""                 # optional cheaper/broader command to find work
    max_iterations: int = 3
    budget_tokens: int = 400_000
    check_timeout: int = 900

    @classmethod
    def load(cls, path: Path) -> "LoopConfig":
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class LoopRunner:
    def __init__(self, repo: Path, config: LoopConfig, make_agent, client,
                 open_pr: bool = False, on_event=print):
        self.repo, self.config = repo.resolve(), config
        self.make_agent, self.on_event, self.open_pr = make_agent, on_event, open_pr
        self.state_file = self.repo / STATE_DIR / f"{config.name}.json"
        self.state = self.load_state()
        self.client = BudgetClient(client, config.budget_tokens, self.state["tokens"])

    # ---- state -------------------------------------------------------------------
    def load_state(self) -> dict:
        if self.state_file.is_file():
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        return {"name": self.config.name, "status": "idle", "iteration": 0, "tokens": 0,
                "branch": "", "base": "", "history": [], "ticks": []}

    def save(self) -> None:
        self.state["tokens"] = self.client.used if hasattr(self, "client") else 0
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_file)

    def log(self, text: str) -> None:
        self.on_event(f"[{self.config.name}] {text}")

    # ---- checks ------------------------------------------------------------------
    def run_check(self, cwd: Path, command: str | None = None) -> tuple[bool, str]:
        done = subprocess.run(command or self.config.check, shell=True, cwd=cwd, text=True,
                              capture_output=True, timeout=self.config.check_timeout,
                              env=clean_env())
        tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-30:])
        return done.returncode == 0, tail

    def worktree(self) -> Path:
        stamp = self.state["branch"].rsplit("/", 1)[-1]
        return self.repo.parent / f"{self.repo.name}-loops" / f"{self.config.name}-{stamp}"

    # ---- one tick ------------------------------------------------------------------
    def tick(self) -> dict:
        s = self.state
        s["ticks"].append(time.strftime("%Y-%m-%dT%H:%M:%S"))
        if s["status"] == "escalated":                # a human must look first (reset)
            self.log("escalated earlier - waiting for a human; run `reset` when done")
            self.save()
            return s
        if s["status"] != "running":                  # a fresh tick: is there work?
            ok, output = self.run_check(self.repo, self.config.discover or None)
            if ok:
                s["status"] = "goal-met"
                self.log("goal already holds - nothing to do")
                self.save()
                return s
            s.update(status="running", iteration=0, history=[], base=git(self.repo,
                     "rev-parse", "HEAD"), branch=f"loop/{self.config.name}/"
                     + time.strftime("%Y%m%d-%H%M%S"), feedback=output)
            self.save()
        path = self.worktree()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            git(self.repo, "worktree", "add", "-b", s["branch"], str(path), s["base"])
        try:
            while s["iteration"] < self.config.max_iterations:
                s["iteration"] += 1
                if self.iteration(path):
                    return s
            self.escalate(f"goal not reached after {self.config.max_iterations} iterations")
        except BudgetExceeded as exc:
            self.escalate(str(exc))
        return s

    def iteration(self, path: Path) -> bool:
        s, n = self.state, self.state["iteration"]
        self.log(f"iteration {n}: maker")
        maker = self.make_agent(path, "maker", self.client)
        summary = maker.run_turn(self.maker_prompt())
        passed, output = self.run_check(path)
        record = {"iteration": n, "maker": summary[:600], "check_passed": passed,
                  "verdict": "", "tokens": self.client.used}
        if passed:
            self.log(f"iteration {n}: check passed - checker")
            diff = git(path, "diff", s["base"])[:20000] or "(no changes)"
            checker = self.make_agent(path, "checker", self.client)
            verdict = checker.run_turn(CHECKER_PROMPT.format(goal=self.config.goal,
                                                             diff=diff)).strip()
            record["verdict"] = verdict[:600]
            if verdict.upper().startswith("APPROVE"):
                s["history"].append(record)
                self.finish(path)
                return True
            s["feedback"] = "The checker rejected the change:\n" + verdict
        else:
            s["feedback"] = "The check still fails:\n" + output
        s["history"].append(record)
        self.save()                                   # checkpoint after every iteration
        return False

    def maker_prompt(self) -> str:
        s = self.state
        earlier = "\n".join(f"- attempt {h['iteration']}: check "
                            f"{'passed' if h['check_passed'] else 'failed'}; "
                            f"{h['verdict'][:150] or h['maker'][:150]}"
                            for h in s["history"][-3:])
        return (f"{self.config.maker_prompt}\n\nGoal: {self.config.goal}\n"
                f"Check command: {self.config.check}\n\nLatest result:\n{s['feedback']}\n"
                + (f"\nEarlier attempts in this loop:\n{earlier}\n" if earlier else "")
                + MAKER_RULES)

    def finish(self, path: Path) -> None:
        s = self.state
        git(path, "add", "-A")
        if git(path, "status", "--porcelain"):
            git(path, "-c", "user.name=minicode-loop", "-c", "user.email=loop@localhost",
                "commit", "-q", "-m", f"[loop] {self.config.name}: {self.config.goal}"
                f"\n\n{s['history'][-1]['maker']}")
        if self.open_pr:
            git(path, "push", "-u", "origin", s["branch"])
            subprocess.run(["gh", "pr", "create", "--draft", "--head", s["branch"],
                            "--title", f"[loop] {self.config.name}",
                            "--body", self.report()], cwd=path, check=True)
        s["status"] = "done"
        self.log(f"done after {s['iteration']} iteration(s), {self.client.used:,} tokens")
        self.save()

    def escalate(self, reason: str) -> None:
        s = self.state
        s["status"], s["escalation"] = "escalated", reason
        note = self.repo / STATE_DIR / f"{self.config.name}-escalation.md"
        note.write_text(f"# Loop {self.config.name} needs a human\n\nReason: {reason}\n\n"
                        f"Branch: {s['branch']} (worktree {self.worktree()})\n\n"
                        + self.report(), encoding="utf-8")
        self.log(f"escalated: {reason}")
        self.save()

    def report(self) -> str:
        rows = "\n".join(f"| {h['iteration']} | {'pass' if h['check_passed'] else 'fail'} | "
                         f"{(h['verdict'].splitlines() or ['-'])[0][:60]} | {h['tokens']:,} |"
                         for h in self.state["history"])
        return (f"Goal: {self.config.goal}\n\n| Iteration | Check | Checker | Tokens |\n"
                f"|---|---|---|---|\n{rows}\n\nAI-generated by a loop: review before merging.")


# ---- the agents --------------------------------------------------------------------
def default_factory(args):
    from minicode.agent import Agent, make_client
    from minicode.hooks import Hooks
    from minicode.security import Policy, Sandbox
    from minicode.tools import Workspace
    from minicode.verify import VerificationGate

    client, model = make_client()

    def make(path: Path, role: str, budget_client):
        workspace = Workspace(path, confirm=lambda _: False)
        workspace.sandbox = Sandbox(args.sandbox, args.sandbox_image)
        agent = Agent(budget_client, model, workspace, max_steps=args.max_steps,
                      on_event=lambda e: print(f"    [{role}] {e.strip()[:100]}"))
        if role == "maker":
            agent.add_hooks(Hooks(path))
            agent.policy = Policy(path, args.mode)
            agent.gate = VerificationGate(path, sandbox=workspace.sandbox)  # tests protected
        else:                                         # the checker only reads
            agent.policy = Policy(path, "read-only")
        return agent
    return make, client


def main() -> None:
    parser = argparse.ArgumentParser(prog="minicode.loop")
    parser.add_argument("--repo", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="one tick of the loop")
    run.add_argument("config")
    run.add_argument("--open-pr", action="store_true")
    run.add_argument("--mode", default="ask")
    run.add_argument("--sandbox", choices=["none", "docker"], default="none")
    run.add_argument("--sandbox-image", default="python:3.12-slim")
    run.add_argument("--max-steps", type=int, default=40)
    status = sub.add_parser("status")
    status.add_argument("name")
    reset = sub.add_parser("reset", help="forget the loop's state (after a human took over)")
    reset.add_argument("name")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()

    if args.command == "run":
        config = LoopConfig.load(repo / args.config)
        make, client = default_factory(args)
        state = LoopRunner(repo, config, make, client, args.open_pr).tick()
        print(f"{config.name}: {state['status']} (iterations {state['iteration']}, "
              f"tokens {state['tokens']:,})")
        raise SystemExit(0 if state["status"] in ("done", "goal-met") else 3)
    file = repo / STATE_DIR / f"{args.name}.json"
    if args.command == "status":
        print(file.read_text(encoding="utf-8") if file.is_file() else "no state yet")
    elif args.command == "reset" and file.is_file():
        file.unlink()
        print(f"{args.name}: state cleared")


if __name__ == "__main__":
    main()
