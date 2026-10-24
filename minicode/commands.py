"""minicode v0.5: custom slash commands - reusable prompts stored in the project.

A file .agents/commands/research.md becomes the command /research. The text
$ARGUMENTS in the file is replaced by whatever you type after the command name.
"""
from pathlib import Path

COMMAND_DIR = Path(".agents") / "commands"


def available(root: Path) -> list[str]:
    folder = root / COMMAND_DIR
    return sorted(p.stem for p in folder.glob("*.md")) if folder.is_dir() else []


def expand(root: Path, name: str, arguments: str) -> str | None:
    """The prompt for /name, or None if the project has no such command."""
    file = root / COMMAND_DIR / f"{name}.md"
    if not file.is_file():
        return None
    template = file.read_text(encoding="utf-8")
    return template.replace("$ARGUMENTS", arguments.strip() or "(no arguments given)")
