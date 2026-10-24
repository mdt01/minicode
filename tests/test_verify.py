"""Tests for the verification gate (Chapter 12)."""
import sys

from minicode.agent import Agent
from minicode.tools import Workspace
from minicode.verify import VerificationGate, compare, fingerprint_source
from tests.test_agent_loop import ScriptedClient, tool_call

TEST_FILE = '''import pytest

def test_total():
    assert total([1, 2]) == 3
    assert total([]) == 0

def test_negative():
    with pytest.raises(ValueError):
        total([-1])
'''


def test_fingerprint_counts_asserts_and_raises():
    tests = fingerprint_source(TEST_FILE)
    assert tests["test_total"] == {"asserts": 2, "skipped": False, "trivial": False}
    assert tests["test_negative"]["asserts"] == 1


def test_weakened_tests_are_reported_and_new_tests_are_fine():
    before = {f"t.py::{k}": v for k, v in fingerprint_source(TEST_FILE).items()}
    weaker = TEST_FILE.replace("    assert total([]) == 0\n", "") \
        .replace("def test_negative", "@pytest.mark.skip\ndef test_negative") \
        + "\ndef test_new():\n    assert True\n"
    after = {f"t.py::{k}": v for k, v in fingerprint_source(weaker).items()}
    problems = compare(before, after)
    assert problems == ["t.py::test_total lost assertions (2 -> 1)",
                        "t.py::test_negative is now skipped"]


def test_gate_sends_the_agent_back_until_tests_are_restored(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text(TEST_FILE)
    client = ScriptedClient([
        tool_call("c1", "edit_file", {"path": "tests/test_calc.py",
                                      "old_text": "    assert total([]) == 0\n",
                                      "new_text": ""}),
        {"content": "Done: all tests pass now."},                 # refused by the gate
        tool_call("c2", "edit_file", {"path": "tests/test_calc.py",
                                      "old_text": "    assert total([1, 2]) == 3\n",
                                      "new_text": "    assert total([1, 2]) == 3\n"
                                                  "    assert total([]) == 0\n"}),
        {"content": "Restored the test; the code needs a fix for empty lists."}])
    agent = Agent(client, "fake", Workspace(tmp_path), on_event=lambda _: None)
    agent.gate = VerificationGate(tmp_path)
    answer = agent.run_turn("make the tests pass")
    assert answer.startswith("Restored the test")
    assert any("lost assertions (2 -> 1)" in str(m.get("content")) for m in agent.messages)


def test_verification_command_must_pass(tmp_path):
    check = f'"{sys.executable}" -c "import os, sys; sys.exit(0 if os.path.exists(\'ok\') else 1)"'
    client = ScriptedClient([
        tool_call("c1", "edit_file", {"path": "app.py", "old_text": "", "new_text": "x = 1"}),
        {"content": "Done."},                                     # refused: check fails
        tool_call("c2", "edit_file", {"path": "ok", "old_text": "", "new_text": "1"}),
        {"content": "Done, verified."}])
    agent = Agent(client, "fake", Workspace(tmp_path), on_event=lambda _: None)
    agent.gate = VerificationGate(tmp_path, command=check)
    assert agent.run_turn("change app.py") == "Done, verified."
