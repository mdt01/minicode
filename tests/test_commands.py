"""v0.5: project commands in .agents/commands/."""
from minicode import commands


def test_commands_are_found_and_expanded(tmp_path):
    folder = tmp_path / ".agents" / "commands"
    folder.mkdir(parents=True)
    (folder / "research.md").write_text("Research how $ARGUMENTS works. Suggest nothing.")
    (folder / "review.md").write_text("Review against $ARGUMENTS")
    assert commands.available(tmp_path) == ["research", "review"]
    assert commands.expand(tmp_path, "research", " event search ") == \
        "Research how event search works. Suggest nothing."
    assert commands.expand(tmp_path, "review", "") == "Review against (no arguments given)"


def test_unknown_command_returns_none(tmp_path):
    assert commands.expand(tmp_path, "deploy", "now") is None
    assert commands.available(tmp_path) == []
