"""minicode v0.10: a verification gate - the agent may not finish until its work checks out.

Two checks run when the model gives its final answer:

1. Test integrity. The test files are fingerprinted when the turn starts. If a test
   was deleted, skipped, lost assertions or became trivial ("assert True"), the
   agent is sent back: weakening a test is not a way to make it pass.
2. A verification command, e.g. "python -m pytest -q" or "make check". If the agent
   changed files and the command fails, the agent is sent back with its output.

    python -m minicode --workspace ../campushub --verify "python -m pytest -q"
"""
import ast
import subprocess
from pathlib import Path

TEST_GLOBS = ("tests/**/test_*.py", "tests/**/*_test.py", "**/tests/test_*.py")


def fingerprint_source(source: str) -> dict[str, dict]:
    """{test name: {"asserts": n, "skipped": bool, "trivial": bool}} for one file."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    tests = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name.startswith("test"):
            asserts = [n for n in ast.walk(node) if isinstance(n, ast.Assert)]
            raises = [n for n in ast.walk(node) if isinstance(n, ast.Attribute)
                      and n.attr == "raises"]                     # pytest.raises(...)
            decorators = " ".join(ast.unparse(d) for d in node.decorator_list)
            body = " ".join(ast.unparse(n) for n in node.body)
            tests[node.name] = {
                "asserts": len(asserts) + len(raises),
                "skipped": "skip" in decorators or "xfail" in decorators
                           or "pytest.skip(" in body,
                "trivial": any(isinstance(a.test, ast.Constant) and a.test.value
                               for a in asserts)}
    return tests


def snapshot(root: Path) -> dict[str, dict]:
    """Fingerprints of every test in the project, keyed "file::test"."""
    found = {}
    files = {p for g in TEST_GLOBS for p in root.glob(g)
             if ".venv" not in p.parts and "node_modules" not in p.parts}
    for file in sorted(files):
        rel = file.relative_to(root).as_posix()
        for name, info in fingerprint_source(file.read_text(encoding="utf-8")).items():
            found[f"{rel}::{name}"] = info
    return found


def compare(before: dict[str, dict], after: dict[str, dict]) -> list[str]:
    """Ways in which the test suite got weaker. New tests are always welcome."""
    problems = []
    for key, old in before.items():
        new = after.get(key)
        if new is None:
            problems.append(f"{key} was deleted")
        elif new["skipped"] and not old["skipped"]:
            problems.append(f"{key} is now skipped")
        elif new["asserts"] < old["asserts"]:
            problems.append(f"{key} lost assertions ({old['asserts']} -> {new['asserts']})")
        elif new["trivial"] and not old["trivial"]:
            problems.append(f"{key} now contains a trivial assertion")
    return problems


class VerificationGate:
    def __init__(self, root: Path, command: str | None = None,
                 protect_tests: bool = True, timeout: int = 600, sandbox=None):
        self.root = root
        self.sandbox = sandbox              # run the check in the agent's sandbox (Ch. 15)
        self.command = command
        self.protect_tests = protect_tests
        self.timeout = timeout
        self.before: dict[str, dict] = {}

    def start(self) -> None:
        """Called at the start of every turn."""
        self.before = snapshot(self.root) if self.protect_tests else {}

    def check(self, changed: set[str]) -> tuple[bool, str]:
        """(passed, message for the model). Called when the model wants to stop."""
        if self.protect_tests:
            problems = compare(self.before, snapshot(self.root))
            if problems:
                return False, ("You weakened the test suite:\n- " + "\n- ".join(problems)
                               + "\nRestore these tests and fix the code instead. If a "
                               "test is really wrong, stop and explain why to the user.")
        if self.command and changed:
            if self.sandbox:
                done = self.sandbox.run(self.command, self.root, timeout=self.timeout)
            else:
                done = subprocess.run(self.command, shell=True, cwd=self.root, text=True,
                                      capture_output=True, timeout=self.timeout)
            if done.returncode != 0:
                tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-20:])
                return False, (f"Verification failed: `{self.command}` exited with "
                               f"{done.returncode}.\n{tail}\nFix the cause, then finish.")
        return True, ""
