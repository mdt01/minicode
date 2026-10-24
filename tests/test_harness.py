"""v0.3 harness features: project rules, plan mode, compaction and tracing."""
import json

from minicode.agent import PLAN_FILE, Agent
from minicode.trace_report import breakdown
from minicode.tools import Workspace
from minicode.tracing import TracingClient
from tests.test_agent_loop import ScriptedClient, tool_call


def make_agent(tmp_path, replies, **kwargs):
    client = ScriptedClient(replies)
    return Agent(client, "fake", Workspace(tmp_path), on_event=lambda _: None, **kwargs), client


def tool_names(request):
    return sorted(t["function"]["name"] for t in request["tools"])


def test_project_rules_are_injected_once(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Run tests with: pytest -q")
    agent, _ = make_agent(tmp_path, [{"content": "ok"}, {"content": "ok"}])
    agent.run_turn("first")
    agent.run_turn("second")
    users = [m["content"] for m in agent.messages if m["role"] == "user"]
    assert "pytest -q" in users[0] and "<system-reminder>" in users[0]
    assert users[1] == "second"


def test_plan_mode_offers_only_read_tools_and_writes_a_plan(tmp_path):
    agent, client = make_agent(tmp_path, [
        tool_call("c1", "write_plan", {"content": "# Plan\n1. Add a test"}),
        {"content": "The plan is ready. Type /go to approve it."},
    ])
    agent.mode = "plan"
    agent.run_turn("Plan a fix for seats_left")
    assert tool_names(client.sent[0]) == ["list_files", "read_file", "write_plan"]
    assert "Plan mode is active" in agent.messages[1]["content"]
    assert (tmp_path / PLAN_FILE).read_text().startswith("# Plan")


def test_plan_mode_is_enforced_by_the_harness(tmp_path):
    agent, _ = make_agent(tmp_path, [
        tool_call("c1", "edit_file", {"path": "x.py", "old_text": "", "new_text": "hi"}),
        {"content": "Sorry."},
    ])
    agent.mode = "plan"
    agent.run_turn("Just do it")
    assert agent.messages[3]["content"].startswith("Error: tool 'edit_file' is not available")
    assert not (tmp_path / "x.py").exists()


def test_go_leaves_plan_mode_and_sends_the_plan(tmp_path):
    agent, client = make_agent(tmp_path, [{"content": "Implemented."}])
    (tmp_path / ".minicode").mkdir()
    (tmp_path / PLAN_FILE).write_text("1. Add a test")
    agent.mode = "plan"
    assert agent.approve_plan() == "Implemented."
    assert agent.mode == "normal"
    assert "run_bash" in tool_names(client.sent[0])
    assert "1. Add a test" in agent.messages[-2]["content"]


def test_manual_compaction_replaces_history_with_summary(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Never edit migrations/.")
    agent, client = make_agent(tmp_path, [
        {"content": "done"},
        {"content": "<analysis>notes</analysis><summary>User fixed seats_left.</summary>"},
    ])
    agent.run_turn("fix seats_left")
    agent.compact()
    assert "tools" not in client.sent[1]                  # no tools while summarizing
    assert [m["role"] for m in agent.messages] == ["system", "user"]
    resume = agent.messages[1]["content"]
    assert "User fixed seats_left." in resume and "notes" not in resume
    assert "Never edit migrations/." in resume            # rules survive compaction


def test_automatic_compaction_when_context_is_nearly_full(tmp_path):
    agent, client = make_agent(tmp_path, [
        {"content": "<summary>Short summary.</summary>"},
        {"content": "Continuing."},
    ], context_limit=1000)
    agent.messages.append({"role": "user", "content": "x" * 4000})   # ~1000 tokens
    assert agent.run_turn("next step") == "Continuing."
    assert "Do not call any tools" in client.sent[0]["messages"][-1]["content"]
    assert "Short summary." in agent.messages[1]["content"]


def test_tracing_client_writes_requests_and_responses(tmp_path):
    inner = ScriptedClient([{"content": "hello"}])
    path = tmp_path / "trace.jsonl"
    agent = Agent(TracingClient(inner, path), "fake", Workspace(tmp_path),
                  on_event=lambda _: None)
    agent.run_turn("hi")
    record = json.loads(path.read_text().splitlines()[0])
    assert record["request"]["messages"][-1]["content"].endswith("hi")
    assert record["response"]["choices"][0]["message"]["content"] == "hello"
    parts = breakdown(record["request"])
    assert parts["system prompt"] > 0 and parts["tools: built-in (4)"] > 0


# ---- v0.4: context management --------------------------------------------------------
def test_clear_keeps_system_prompt_and_resends_rules(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Use python -m pytest -q")
    agent, _ = make_agent(tmp_path, [{"content": "a"}, {"content": "b"}])
    agent.run_turn("first")
    agent.clear()
    assert [m["role"] for m in agent.messages] == ["system"]
    agent.run_turn("second")
    assert "python -m pytest -q" in agent.messages[1]["content"]


def test_handoff_writes_notes_and_starts_fresh(tmp_path):
    agent, client = make_agent(tmp_path, [
        {"content": "ok"}, {"content": "# Goal\nFix seats_left"}, {"content": "continuing"}])
    agent.run_turn("fix seats_left")
    agent.handoff()
    assert "tools" not in client.sent[1]
    assert (tmp_path / ".minicode/progress.md").read_text().startswith("# Goal")
    agent.run_turn("go on")
    assert len(agent.messages) == 3 and "Fix seats_left" in agent.messages[1]["content"]
    assert "Fix seats_left" not in agent.messages[0]["content"]


def test_compact_instructions_come_from_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# X\n## Compact instructions\nKeep test output.\n## Other\nno")
    agent, client = make_agent(tmp_path, [{"content": "<summary>s</summary>"}])
    agent.compact()
    prompt = client.sent[0]["messages"][-1]["content"]
    assert "Keep test output." in prompt and "no" not in prompt.split("Keep test output.")[1]


def test_meter_and_zones(tmp_path):
    from minicode.context import meter, zone
    agent, _ = make_agent(tmp_path, [])
    names = [name for name, _ in meter(agent.request_preview())]
    assert names[0].startswith("tools") or names[0] == "system prompt"
    assert zone(10, 100) == "smart zone" and zone(80, 100).startswith("dumb")
