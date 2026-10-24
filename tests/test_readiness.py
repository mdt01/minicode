"""Tests for the agent-readiness scorecard (Chapter 11)."""
from pathlib import Path

from minicode import readiness
from minicode.readiness import Repo, audit, report


def make_repo(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def check(result: dict, name: str) -> dict:
    return next(c for c in result["checks"] if c["check"] == name)


BASIC = {
    "README.md": "# Demo\n## Setup\npip install -r requirements.txt\n## Test\npytest\n",
    "requirements.txt": "fastapi==0.115.0\n",
    "app/main.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
    "tests/test_main.py": "def test_add():\n    assert 1 + 1 == 2\n",
    ".gitignore": ".env\n",
}


def test_bare_repository_is_not_functional(tmp_path):
    result = audit(make_repo(tmp_path, {"main.py": "print('hi')\n"}))
    assert result["level"] == 0
    assert not check(result, "README")["passed"]


def test_gaps_are_found_and_explained(tmp_path):
    files = dict(BASIC)
    files["app/config.py"] = 'import os\nSECRET_KEY = "dev-secret-123"\nDB = os.getenv("DB")\n'
    files["app/search.py"] = 'def q(t: str) -> str:\n    return f"SELECT * FROM e WHERE t=\'{t}\'"\n'
    result = audit(make_repo(tmp_path, files))
    assert "app/config.py:2" in check(result, "No hard-coded secrets")["detail"]
    assert not check(result, "No SQL built from strings")["passed"]
    assert not check(result, "Environment variables documented")["passed"]
    assert check(result, "Type hints (80%+)")["passed"]
    assert "Gaps, lowest level first" in report(result)


def test_levels_are_gated_by_the_basics(tmp_path):
    files = dict(BASIC)
    files["AGENTS.md"] = "# Demo\n"
    files["CONTRIBUTING.md"] = "Run make check.\n"
    files["pyproject.toml"] = "[tool.pytest.ini_options]\n[tool.ruff]\n[tool.ruff.format]\n"
    files[".github/workflows/ci.yml"] = "run: pytest\n"
    result = audit(make_repo(tmp_path, files))
    assert result["level"] >= 2
    (tmp_path / "README.md").unlink()                     # break two level-1 checks:
    (tmp_path / ".gitignore").unlink()                    # 3 of 5 is below 80%
    assert audit(tmp_path)["level"] == 0


def test_flaky_suite_is_detected(tmp_path):
    repo = Repo(make_repo(tmp_path, BASIC), test_runs=3)
    repo._test_results = [(True, 1.0), (False, 1.1), (True, 0.9)]   # pretend runs
    ok, detail = readiness.tests_stable(repo)
    assert not ok and "failed 1 of 3" in detail
