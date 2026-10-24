"""v0.8: skills with progressive disclosure, explicit invocation, and headless mode."""
import json
import subprocess
import sys

from minicode.agent import Agent
from minicode.skills import SkillLibrary
from minicode.tools import Workspace
from tests.test_agent_loop import ScriptedClient, tool_call

SKILL = """---
name: verify-change
description: Verify a CampusHub change. Use after editing code, before saying "done".
---
# Verify a change
1. Run python -m pytest -q
2. Run python .agents/skills/verify-change/scripts/smoke.py
"""


def make_library(tmp_path):
    folder = tmp_path / ".agents" / "skills" / "verify-change"
    (folder / "scripts").mkdir(parents=True)
    (folder / "SKILL.md").write_text(SKILL)
    (folder / "scripts" / "smoke.py").write_text("print('ok')\n")
    hidden = tmp_path / ".agents" / "skills" / "grill-me"
    hidden.mkdir()
    (hidden / "SKILL.md").write_text("---\nname: grill-me\ndescription: Interview me\n"
                                     "disable-model-invocation: true\n---\nAsk questions.")
    return SkillLibrary(tmp_path, personal_dir=tmp_path / "none")


def test_three_levels_of_disclosure(tmp_path):
    library = make_library(tmp_path)
    assert "verify-change: Verify a CampusHub change" in library.listing()
    assert "grill-me" not in library.listing()                 # user-only skill
    body = library.load_skill("verify-change")
    assert "Run python -m pytest -q" in body and "scripts/smoke.py" in body
    assert library.read_skill_file("verify-change", "scripts/smoke.py") == "print('ok')\n"


def test_skill_files_are_sandboxed(tmp_path):
    library = make_library(tmp_path)
    (tmp_path / "secret.txt").write_text("x")
    try:
        library.read_skill_file("verify-change", "../../../secret.txt")
        assert False, "should refuse"
    except Exception as exc:
        assert "is not a file in skill" in str(exc)


def test_agent_lists_then_loads_skill(tmp_path):
    client = ScriptedClient([tool_call("c1", "load_skill", {"name": "verify-change"}),
                             {"content": "Verified."}])
    agent = Agent(client, "fake", Workspace(tmp_path), on_event=lambda _: None)
    agent.add_skills(make_library(tmp_path))
    agent.run_turn("I changed utils.py, please check it")
    assert "verify-change:" in agent.messages[1]["content"]          # level 1
    assert "Run python -m pytest -q" in agent.messages[3]["content"]  # level 2
    tools = [t["function"]["name"] for t in client.sent[0]["tools"]]
    assert "load_skill" in tools and "read_skill_file" in tools


def test_user_can_invoke_hidden_skill(tmp_path):
    prompt = make_library(tmp_path).invoke("grill-me", "email reminders")
    assert "Ask questions." in prompt and "Arguments: email reminders" in prompt


def test_headless_mode_reports_errors_as_exit_code(tmp_path):
    env = {"LLM_API_KEY": "k", "LLM_MODEL": "m", "LLM_BASE_URL": "http://127.0.0.1:9/v1",
           "PATH": "", "PYTHONPATH": "."}
    done = subprocess.run([sys.executable, "-m", "minicode", "--workspace", str(tmp_path),
                           "-p", "hello", "--json"], capture_output=True, text=True, env=env,
                          timeout=60)
    assert done.returncode == 1
    assert "error" in json.loads(done.stderr)
