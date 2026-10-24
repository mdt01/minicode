"""Tests for the multi-agent review pipeline (Chapter 13), with a fake model."""
import json
import subprocess
import threading

from minicode.review import aggregate, get_diff, review, score, to_markdown
from tests.test_agent_loop import completion


class FakeReviewModel:
    """Answers by role: each specialist reports fixed findings; the verifier drops nits."""

    def __init__(self):
        self.chat = self.completions = self
        self.lock = threading.Lock()
        self.calls = []

    def create(self, model, messages):
        system, user = messages[0]["content"], messages[1]["content"]
        with self.lock:
            self.calls.append(system[:40])
        if "verify one code-review finding" in system:
            verdict = "drop" if "trailing" in user else "keep"
            return completion({"content": json.dumps({"verdict": verdict, "reason": "x"})})
        if "security only" in system:
            found = [{"file": "app.py", "line": 3, "severity": "blocker",
                      "title": "SQL injection in venue filter", "evidence": "f-string SQL"}]
        elif "correctness only" in system:
            found = [{"file": "app.py", "line": 4, "severity": "major",
                      "title": "venue filter SQL injection possible", "evidence": "same"},
                     {"file": "app.py", "line": 9, "severity": "major",
                      "title": "capacity may go below registrations", "evidence": "no check"}]
        else:
            found = [{"file": "app.py", "line": 12, "severity": "nit",
                      "title": "trailing whitespace", "evidence": "style"}]
        return completion({"content": "```json\n" + json.dumps({"findings": found}) + "\n```"})


def make_repo(tmp_path):
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@x"), git("config", "user.name", "t")
    (tmp_path / "app.py").write_text("def ok():\n    return 1\n")
    git("add", "."), git("commit", "-qm", "base")
    (tmp_path / "app.py").write_text("\n".join(f"line {i}" for i in range(1, 15)) + "\n")
    return tmp_path


def test_pipeline_merges_duplicates_and_verifier_drops_noise(tmp_path):
    repo = make_repo(tmp_path)
    diff = get_diff(repo, "main")
    assert "+line 3" in diff
    model = FakeReviewModel()
    kept, dropped, tokens = review(model, "fake", repo, diff,
                                   ["security", "correctness", "consistency"])
    assert [(f["line"], f["severity"]) for f in kept] == [(3, "blocker"), (9, "major")]
    assert kept[0]["reviewers"] == ["correctness", "security"]      # merged duplicate
    assert dropped == 1                                              # the nit
    assert tokens["input"] == 100 * len(model.calls)
    assert "| BLOCKER | `app.py:3`" in to_markdown(kept, dropped, tokens)


def test_different_problems_on_nearby_lines_are_not_merged():
    findings = [{"file": "a.py", "line": 56, "severity": "major", "reviewers": ["c"],
                 "title": "returns error dict instead of 404"},
                {"file": "a.py", "line": 57, "severity": "major", "reviewers": ["c"],
                 "title": "capacity may drop below registrations"}]
    assert len(aggregate(findings)) == 2


def test_score_counts_each_finding_once():
    expected = [{"id": "B2", "file": "a.py", "line": 56},
                {"id": "B3", "file": "a.py", "line": 57},
                {"id": "B5", "file": "b.py", "line": 37}]
    findings = [{"file": "a.py", "line": 56}, {"file": "c.py", "line": 1}]
    result = score(findings, expected)
    assert result["caught"] == ["B2"] and result["missed"] == ["B3", "B5"]
    assert result["other_findings"] == 1
