"""minicode v0.9.1: an agent-readiness scorecard for any repository.

    python -m minicode.readiness ../campushub
    python -m minicode.readiness ../campushub --run-tests 5 --json

Every check is deterministic: it looks at files, or runs the test command.
Judgement calls (are the docs accurate? are the module boundaries clear?) are left
to the agent-driven audit prompt in the book (Chapter 11).

Levels follow the five-step maturity ladder: 1 Functional, 2 Documented,
3 Standardized, 4 Optimized, 5 Autonomous. A repository reaches a level when at
least 80% of the checks at that level and every level below it pass.
"""
import argparse
import ast
import json
import re
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

LEVELS = {1: "Functional", 2: "Documented", 3: "Standardized", 4: "Optimized",
          5: "Autonomous"}
PILLARS = ["Style & validation", "Build system", "Testing", "Documentation",
           "Dev environment", "Debugging & observability", "Security",
           "Task discovery", "Product & experimentation"]
SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
        "dist", "build", ".mypy_cache", ".ruff_cache"}
UNLOCK = 0.8                                  # share of checks needed per level
BIG_FILE = 500                                # lines


@dataclass
class Check:
    pillar: str
    level: int
    name: str
    test: Callable[["Repo"], tuple[bool, str]]


class Repo:
    """Cached facts about a repository, shared by all checks."""

    def __init__(self, root: Path, test_runs: int = 0, python: str | None = None):
        self.root = root.resolve()
        self.python_exe = python or self.find_python()
        self.test_runs = test_runs
        self._test_results: list[tuple[bool, float]] | None = None
        self.files = [p for p in self.root.rglob("*")
                      if p.is_file() and not SKIP & set(p.relative_to(self.root).parts)]
        self.python = [p for p in self.files if p.suffix == ".py"]
        self.source = [p for p in self.python
                       if not self.is_test(p) and p.name != "__init__.py"]

    def find_python(self) -> str:
        """The project's own virtual environment if it has one, else this Python."""
        for venv in (".venv", "venv"):
            for exe in ("Scripts/python.exe", "bin/python"):
                if (self.root / venv / exe).is_file():
                    return str(self.root / venv / exe)
        return sys.executable

    def is_test(self, path: Path) -> bool:
        rel = path.relative_to(self.root).as_posix()
        return rel.startswith("tests/") or path.name.startswith("test_")

    def exists(self, *patterns: str) -> bool:
        return any(any(self.root.glob(p)) for p in patterns)

    def text(self, name: str) -> str:
        file = self.root / name
        return file.read_text(encoding="utf-8", errors="ignore") if file.is_file() else ""

    def grep(self, pattern: str, files: list[Path]) -> list[str]:
        hits, rx = [], re.compile(pattern)
        for f in files:
            for n, line in enumerate(f.read_text(encoding="utf-8", errors="ignore")
                                     .splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f.relative_to(self.root).as_posix()}:{n}")
        return hits

    def tests(self) -> list[tuple[bool, float]]:
        """Run the test suite test_runs times: (passed, seconds) per run."""
        if self._test_results is None:
            self._test_results = []
            for _ in range(self.test_runs):
                start = time.perf_counter()
                done = subprocess.run([self.python_exe, "-m", "pytest", "-q", "-p",
                                       "no:cacheprovider"], cwd=self.root,
                                      capture_output=True, text=True)
                self._test_results.append((done.returncode == 0,
                                           time.perf_counter() - start))
        return self._test_results


# ---- the checks --------------------------------------------------------------------
def has(*patterns: str, why: str = "") -> Callable[[Repo], tuple[bool, str]]:
    return lambda r: (r.exists(*patterns), why or "missing: " + ", ".join(patterns))


def readme_explains(r: Repo) -> tuple[bool, str]:
    text = r.text("README.md").lower()
    ok = ("install" in text or "setup" in text or "getting started" in text) \
        and ("test" in text)
    return ok, "README has no setup and test instructions"


def env_documented(r: Repo) -> tuple[bool, str]:
    used = r.grep(r"os\.(getenv|environ)", r.source)
    if not used:
        return True, ""
    return r.exists(".env.example", ".env.sample"), \
        f"settings read from the environment ({used[0]}, ...), but no .env.example"


def one_test_command(r: Repo) -> tuple[bool, str]:
    runners = ["Makefile", "justfile", "noxfile.py", "tox.ini", "Taskfile.yml"]
    if r.exists(*runners) or "[tool.pytest" in r.text("pyproject.toml"):
        return True, ""
    if "scripts" in r.text("package.json"):
        return True, ""
    return False, "no task runner or pytest config: the test command is a guess"


def linter(r: Repo) -> tuple[bool, str]:
    py = r.text("pyproject.toml")
    ok = "[tool.ruff" in py or "[tool.flake8" in py or "[tool.pylint" in py \
        or r.exists("ruff.toml", ".ruff.toml", ".flake8", ".eslintrc*", "eslint.config.*")
    return ok, "no linter configuration"


def formatter(r: Repo) -> tuple[bool, str]:
    py = r.text("pyproject.toml")
    ok = "[tool.ruff.format" in py or "[tool.black" in py \
        or r.exists(".prettierrc*", ".editorconfig")
    return ok, "no formatter configuration"


def type_hints(r: Repo) -> tuple[bool, str]:
    total = typed = 0
    for f in r.source:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                total += 1
                args = [a for a in node.args.args if a.arg not in ("self", "cls")]
                if node.returns is not None and all(a.annotation for a in args):
                    typed += 1
    share = typed / total if total else 1.0
    return share >= 0.8, f"only {share:.0%} of functions fully typed"


def type_checker(r: Repo) -> tuple[bool, str]:
    py = r.text("pyproject.toml")
    ok = "[tool.mypy" in py or "[tool.pyright" in py \
        or r.exists("mypy.ini", "pyrightconfig.json", "tsconfig.json")
    return ok, "no type checker configured"


def ci_runs(word: str) -> Callable[[Repo], tuple[bool, str]]:
    def check(r: Repo) -> tuple[bool, str]:
        text = " ".join(p.read_text(encoding="utf-8", errors="ignore").lower()
                        for p in r.root.glob(".github/workflows/*.y*ml"))
        return word in text, f"CI does not run {word}"
    return check


def pinned(r: Repo) -> tuple[bool, str]:
    lines = [ln.strip() for ln in r.text("requirements.txt").splitlines()
             if ln.strip() and not ln.startswith("#")]
    if r.exists("poetry.lock", "uv.lock", "Pipfile.lock", "package-lock.json"):
        return True, ""
    ok = bool(lines) and all("==" in ln for ln in lines)
    return ok, "dependencies are not pinned"


def no_big_files(r: Repo) -> tuple[bool, str]:
    big = [f.relative_to(r.root).as_posix() for f in r.source
           if len(f.read_text(encoding="utf-8", errors="ignore").splitlines()) > BIG_FILE]
    return not big, f"files over {BIG_FILE} lines: {', '.join(big[:3])}"


def tests_pass(r: Repo) -> tuple[bool, str]:
    runs = r.tests()
    if not runs:
        return True, "(not run; use --run-tests)"
    return runs[0][0], "the test suite fails"


def tests_fast(r: Repo) -> tuple[bool, str]:
    runs = r.tests()
    if not runs:
        return True, "(not run)"
    slowest = max(t for _, t in runs)
    return slowest < 60, f"test suite takes {slowest:.0f}s (target: under 60s)"


def tests_stable(r: Repo) -> tuple[bool, str]:
    runs = r.tests()
    if len(runs) < 2:
        return True, "(needs --run-tests 2 or more)"
    results = {ok for ok, _ in runs}
    fails = sum(not ok for ok, _ in runs)
    return len(results) == 1, f"flaky: failed {fails} of {len(runs)} identical runs"


def test_ratio(r: Repo) -> tuple[bool, str]:
    tests = [p for p in r.python if r.is_test(p) and p.name != "__init__.py"]
    ratio = len(tests) / max(1, len(r.source))
    return ratio >= 0.3, f"{len(tests)} test files for {len(r.source)} source files"


def no_secrets(r: Repo) -> tuple[bool, str]:
    pattern = (r"(?i)(secret|password|token|api_key)\w*\s*=\s*['\"][^'\"]{6,}['\"]"
               r"|getenv\([^)]*(token|secret|key)[^)]*,\s*['\"][^'\"]{6,}")
    hits = r.grep(pattern, r.source)
    return not hits, f"possible hard-coded secrets: {', '.join(hits[:3])}"


def env_ignored(r: Repo) -> tuple[bool, str]:
    return ".env" in r.text(".gitignore"), ".env is not in .gitignore"


def raw_sql(r: Repo) -> tuple[bool, str]:
    hits = r.grep(r"f[\"'].*\b(SELECT|INSERT|UPDATE|DELETE)\b", r.source)
    return not hits, f"SQL built with f-strings: {', '.join(hits[:3])}"


def uses_logging(r: Repo) -> tuple[bool, str]:
    pattern = r"getLogger\(__name__\)|logging\.basicConfig|\blog(ger)?\.(info|warning|error)\("
    return bool(r.grep(pattern, r.source)), "application code never logs"


def health_endpoint(r: Repo) -> tuple[bool, str]:
    return bool(r.grep(r"[\"']/health", r.source)), "no /health endpoint"


def agent_config(r: Repo) -> tuple[bool, str]:
    ok = r.exists(".agents/hooks.json", ".claude/settings.json", ".agents/skills/*",
                  ".claude/skills/*")
    return ok, "no agent hooks or skills in the repository"


CHECKS = [
    # level 1: functional
    Check("Documentation", 1, "README", has("README.md", "README.rst")),
    Check("Build system", 1, "Dependency manifest",
          has("requirements*.txt", "pyproject.toml", "package.json", "go.mod")),
    Check("Testing", 1, "Tests exist", has("tests/*", "test/*", "**/test_*.py")),
    Check("Testing", 1, "Tests pass", tests_pass),
    Check("Security", 1, ".env ignored by Git", env_ignored),
    # level 2: documented
    Check("Documentation", 2, "Setup and test steps in README", readme_explains),
    Check("Documentation", 2, "Agent file", has("AGENTS.md", "CLAUDE.md",
                                                why="no AGENTS.md or CLAUDE.md")),
    Check("Dev environment", 2, "Environment variables documented", env_documented),
    Check("Task discovery", 2, "Contributing guide",
          has("CONTRIBUTING.md", "docs/CONTRIBUTING.md")),
    Check("Security", 2, "No hard-coded secrets", no_secrets),
    # level 3: standardized
    Check("Style & validation", 3, "Linter configured", linter),
    Check("Style & validation", 3, "Formatter configured", formatter),
    Check("Build system", 3, "One test command", one_test_command),
    Check("Build system", 3, "Pinned dependencies", pinned),
    Check("Testing", 3, "CI runs the tests", ci_runs("pytest")),
    Check("Testing", 3, "Enough test files", test_ratio),
    Check("Security", 3, "No SQL built from strings", raw_sql),
    Check("Debugging & observability", 3, "Application logging", uses_logging),
    # level 4: optimized
    Check("Style & validation", 4, "Type hints (80%+)", type_hints),
    Check("Style & validation", 4, "Type checker configured", type_checker),
    Check("Dev environment", 4, "Reproducible environment",
          has(".devcontainer/*", "Dockerfile", "flake.nix",
              why="no dev container or Dockerfile")),
    Check("Testing", 4, "Tests under 60 seconds", tests_fast),
    Check("Testing", 4, "Tests are deterministic", tests_stable),
    Check("Style & validation", 4, "No giant files", no_big_files),
    Check("Debugging & observability", 4, "Health endpoint", health_endpoint),
    Check("Task discovery", 4, "Issue and PR templates",
          has(".github/ISSUE_TEMPLATE/*", ".github/pull_request_template.md",
              why="no issue or pull-request templates")),
    # level 5: autonomous
    Check("Style & validation", 5, "CI runs the linter", ci_runs("ruff")),
    Check("Dev environment", 5, "Agent hooks and skills", agent_config),
    Check("Task discovery", 5, "Code owners", has(".github/CODEOWNERS", "CODEOWNERS")),
    Check("Product & experimentation", 5, "Changelog", has("CHANGELOG.md")),
    Check("Product & experimentation", 5, "Feature flags",
          lambda r: (bool(r.grep(r"(?i)feature_?flag|FEATURE_", r.source)),
                     "no feature flags")),
]


# ---- scoring -----------------------------------------------------------------------
def audit(root: Path, test_runs: int = 0, python: str | None = None) -> dict:
    repo = Repo(root, test_runs, python)
    results = []
    for check in CHECKS:
        ok, why = check.test(repo)
        results.append({"pillar": check.pillar, "level": check.level,
                        "check": check.name, "passed": ok, "detail": "" if ok else why})
    level = 0
    for n in LEVELS:
        upto = [r for r in results if r["level"] <= n]
        if sum(r["passed"] for r in upto) / len(upto) < UNLOCK:
            break
        level = n
    pillars = {p: [sum(r["passed"] for r in results if r["pillar"] == p),
                   sum(r["pillar"] == p for r in results)] for p in PILLARS}
    return {"repository": repo.root.name, "level": level,
            "level_name": LEVELS.get(level, "Not yet functional"),
            "passed": sum(r["passed"] for r in results), "total": len(results),
            "pillars": pillars, "checks": results}


def report(result: dict) -> str:
    lines = [f"Agent readiness: {result['repository']}    "
             f"level {result['level']} of 5 ({result['level_name']}), "
             f"{result['passed']}/{result['total']} checks", ""]
    for pillar, (ok, total) in result["pillars"].items():
        lines.append(f"  {pillar:<28}{ok}/{total}  " + "#" * ok + "." * (total - ok))
    gaps = sorted((r for r in result["checks"] if not r["passed"]),
                  key=lambda r: r["level"])
    if gaps:
        lines += ["", "Gaps, lowest level first:"]
        for g in gaps:
            lines += textwrap.wrap(f"L{g['level']} {g['check']}: {g['detail']}", 76,
                                   initial_indent="  ", subsequent_indent="      ")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(prog="minicode.readiness")
    parser.add_argument("path", nargs="?", default=".")
    parser.add_argument("--run-tests", type=int, default=0, metavar="N",
                        help="run the test suite N times (speed and flakiness)")
    parser.add_argument("--python", help="interpreter for the tests (default: the "
                        "project's .venv, else this one)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = audit(Path(args.path), args.run_tests, args.python)
    print(json.dumps(result, indent=2) if args.json else report(result))


if __name__ == "__main__":
    main()
