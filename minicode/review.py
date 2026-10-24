"""minicode v0.11: multi-agent code review - specialists, a verification pass, an aggregator.

    python -m minicode.review --base main                     # before you push
    python -m minicode.review --base origin/main --markdown review.md    # in CI
    python -m minicode.review --base main --expect seeded-bugs.json      # catch rate

1. Specialists (security, correctness, consistency by default) each read the diff
   plus the team's review rules and return findings as JSON. They run in parallel.
2. A verifier looks at every finding next to the real code and keeps or drops it.
   This pass exists to cut false positives - the main reason teams mute review bots.
3. The aggregator merges duplicates, sorts by severity and writes one report.
"""
import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SEVERITIES = ["blocker", "major", "minor", "nit"]
RULES_FILE = Path(".agents") / "review-rules.md"

SPECIALISTS = {
    "security": "You review for security only: injection (SQL, shell, path), missing "
                "authentication or authorisation, secrets in code, unsafe input "
                "handling, data exposure.",
    "correctness": "You review for correctness only: logic errors, wrong conditions, "
                   "boundary cases, broken business rules, error handling, state that "
                   "can become invalid, behaviour that contradicts the spec.",
    "consistency": "You review for consistency with the existing codebase only: API "
                   "conventions, error style, naming, layering (routers, services, "
                   "schemas), missing tests for new behaviour.",
}

FINDINGS_FORMAT = """Reply with JSON only, no prose:
{"findings": [{"file": "app/x.py", "line": 12, "severity": "blocker|major|minor|nit",
  "title": "short summary", "evidence": "the code and why it is wrong",
  "suggestion": "how to fix it"}]}
Report only problems in lines the diff adds or changes. Line numbers refer to the new
version of the file. If you find nothing, reply {"findings": []}. Do not pad."""

VERIFY_PROMPT = """You verify one code-review finding. Look at the code and decide whether
the finding is real (a genuine problem a careful senior engineer would want fixed) or a
false positive (wrong, speculative, already handled, or pure taste).
Reply with JSON only: {"verdict": "keep" or "drop", "reason": "one sentence"}"""


# ---- inputs ------------------------------------------------------------------------
def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def get_diff(root: Path, base: str) -> str:
    """Changes since base, including uncommitted work (so you can review pre-push)."""
    return git(root, "diff", "--unified=5", base, "--", ".")


def review_context(root: Path, spec: str | None = None) -> str:
    parts = []
    for name, title in ((RULES_FILE, "Team review rules"), (Path("AGENTS.md"), "AGENTS.md")):
        file = root / name
        if file.is_file():
            parts.append(f"## {title}\n{file.read_text(encoding='utf-8').strip()}")
    if spec:
        parts.append(f"## The spec or plan this change implements\n"
                     f"{Path(spec).read_text(encoding='utf-8').strip()}")
    return "\n\n".join(parts)


def code_around(root: Path, file: str, line: int, radius: int = 15) -> str:
    path = root / file
    if not path.is_file():
        return "(file not found)"
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    start = max(0, line - radius - 1)
    return "\n".join(f"{n:>4}  {text}" for n, text in
                     enumerate(lines[start:line + radius], start + 1))


# ---- model calls -------------------------------------------------------------------
class Reviewer:
    def __init__(self, client, model: str, root: Path):
        self.client, self.model, self.root = client, model, root
        self.tokens = {"input": 0, "output": 0}

    def ask_json(self, system: str, user: str) -> dict:
        response = self.client.chat.completions.create(
            model=self.model, messages=[{"role": "system", "content": system},
                                        {"role": "user", "content": user}])
        if response.usage:
            self.tokens["input"] += response.usage.prompt_tokens
            self.tokens["output"] += response.usage.completion_tokens
        text = response.choices[0].message.content or ""
        match = re.search(r"\{.*\}", text, re.DOTALL)      # tolerate ```json fences
        try:
            return json.loads(match.group(0)) if match else {}
        except json.JSONDecodeError:
            return {}

    def specialist(self, name: str, diff: str, context: str) -> list[dict]:
        system = (f"You are a code reviewer. {SPECIALISTS[name]}\n"
                  f"Ignore everything outside your speciality.\n\n{FINDINGS_FORMAT}")
        user = f"{context}\n\n## The diff to review\n{diff}"
        findings = self.ask_json(system, user).get("findings", [])
        clean = []
        for f in findings if isinstance(findings, list) else []:
            if not isinstance(f, dict) or "file" not in f:
                continue
            f["severity"] = f.get("severity") if f.get("severity") in SEVERITIES else "minor"
            f["line"] = int(f.get("line") or 0)
            f["reviewers"] = [name]
            clean.append(f)
        return clean

    def verify(self, finding: dict) -> dict:
        user = (f"Finding ({finding['severity']}): {finding.get('title', '')}\n"
                f"Evidence given: {finding.get('evidence', '')}\n\n"
                f"Code in {finding['file']} around line {finding['line']}:\n"
                f"{code_around(self.root, finding['file'], finding['line'])}")
        answer = self.ask_json(VERIFY_PROMPT, user)
        finding["verdict"] = "drop" if answer.get("verdict") == "drop" else "keep"
        finding["verdict_reason"] = answer.get("reason", "")
        return finding


# ---- aggregation and reporting ----------------------------------------------------------
def similar(a: str, b: str) -> bool:
    """Do two finding titles describe the same problem? (word overlap)"""
    wa, wb = (set(re.findall(r"[a-z]{3,}", t.lower())) for t in (a, b))
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.3


def aggregate(findings: list[dict], window: int = 3) -> list[dict]:
    """Merge duplicates (same file, nearby line, similar title); keep the most severe."""
    merged: list[dict] = []
    rank = SEVERITIES.index
    for f in sorted(findings, key=lambda f: rank(f["severity"])):
        for m in merged:
            if m["file"] == f["file"] and abs(m["line"] - f["line"]) <= window \
                    and similar(m.get("title", ""), f.get("title", "")):
                m["reviewers"] = sorted(set(m["reviewers"]) | set(f["reviewers"]))
                break
        else:
            merged.append(dict(f))
    return sorted(merged, key=lambda f: (rank(f["severity"]), f["file"], f["line"]))


def to_markdown(findings: list[dict], dropped: int, tokens: dict) -> str:
    if not findings:
        body = "No problems found."
    else:
        rows = [f"| {f['severity'].upper()} | `{f['file']}:{f['line']}` | "
                f"{f.get('title', '')} |" for f in findings]
        body = "\n".join(["| Severity | Where | Finding |", "|---|---|---|", *rows])
        details = [f"**{f['file']}:{f['line']} - {f.get('title', '')}** "
                   f"(found by {', '.join(f['reviewers'])})  \n"
                   f"{f.get('evidence', '')}  \n*Suggestion:* {f.get('suggestion', '')}"
                   for f in findings]
        body += "\n\n" + "\n\n".join(details)
    return (f"## AI review\n\n{body}\n\n_{dropped} finding(s) dropped by the verification "
            f"pass. Tokens: {tokens['input']:,} in, {tokens['output']:,} out. "
            f"AI review assists; a human approves._\n")


def score(findings: list[dict], expected: list[dict], window: int = 3) -> dict:
    """Compare with known (seeded) bugs: catch rate, and findings to triage by hand."""
    caught, matched = [], set()
    for bug in expected:
        for i, f in enumerate(findings):
            if i not in matched and f["file"] == bug["file"] \
                    and abs(f["line"] - bug["line"]) <= window:
                caught.append(bug["id"])
                matched.add(i)
                break
    others = [f for i, f in enumerate(findings) if i not in matched]
    return {"caught": caught, "missed": [b["id"] for b in expected if b["id"] not in caught],
            "catch_rate": len(caught) / len(expected) if expected else 1.0,
            "other_findings": len(others)}


def review(client, model: str, root: Path, diff: str, specialists: list[str],
           context: str = "", verify: bool = True) -> tuple[list[dict], int, dict]:
    """The whole pipeline. Returns (kept findings, number dropped, tokens)."""
    reviewer = Reviewer(client, model, root)
    with ThreadPoolExecutor(max_workers=8) as pool:
        batches = list(pool.map(lambda n: reviewer.specialist(n, diff, context), specialists))
        findings = aggregate([f for batch in batches for f in batch])
        if verify:
            findings = list(pool.map(reviewer.verify, findings))
    kept = [f for f in findings if f.get("verdict", "keep") == "keep"]
    return kept, len(findings) - len(kept), reviewer.tokens


def main() -> None:
    from minicode.agent import make_client
    parser = argparse.ArgumentParser(prog="minicode.review")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", default="main", help="branch or commit to compare with")
    parser.add_argument("--spec", help="spec or plan file the change implements")
    parser.add_argument("--specialists", default=",".join(SPECIALISTS))
    parser.add_argument("--no-verify-pass", action="store_true")
    parser.add_argument("--markdown", help="also write the report to this file")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--expect", help="JSON list of known bugs: report the catch rate")
    parser.add_argument("--fail-on", default="blocker", choices=SEVERITIES + ["never"])
    args = parser.parse_args()

    root = Path(args.repo).resolve()
    diff = get_diff(root, args.base)
    if not diff.strip():
        print("Nothing to review: no changes against", args.base)
        return
    client, model = make_client()
    names = [n.strip() for n in args.specialists.split(",") if n.strip() in SPECIALISTS]
    kept, dropped, tokens = review(client, model, root, diff, names,
                                   review_context(root, args.spec), not args.no_verify_pass)
    report = to_markdown(kept, dropped, tokens)
    if args.markdown:
        Path(args.markdown).write_text(report, encoding="utf-8")
    if args.expect:
        result = score(kept, json.loads(Path(args.expect).read_text(encoding="utf-8")))
        report += (f"\nSeeded bugs caught: {len(result['caught'])}/"
                   f"{len(result['caught']) + len(result['missed'])} "
                   f"({result['catch_rate']:.0%}); missed: {', '.join(result['missed']) or '-'}; "
                   f"other findings to triage: {result['other_findings']}\n")
    print(json.dumps(kept, indent=2) if args.json else report)
    if args.fail_on != "never":
        limit = SEVERITIES.index(args.fail_on)
        if any(SEVERITIES.index(f["severity"]) <= limit for f in kept):
            sys.exit(1)


if __name__ == "__main__":
    main()
