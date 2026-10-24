"""minicode v0.4: measure the context window and move context to disk."""
from pathlib import Path

from minicode.trace_report import breakdown

SMART, DUMB = 0.5, 0.75          # rules of thumb: quality starts to drop past half full

HANDOFF_PROMPT = """Do not call any tools. Reply with plain text only.

Write a handoff note in Markdown so that a new session, with no memory of this \
one, can continue the work. Use these headings:

# Goal
# Done so far (files changed, tests run, results)
# Decisions and why
# Open problems
# Next steps (numbered, smallest first)

Be concrete: name files, functions and commands. Copy any rule the user gave \
word for word. Keep it under 400 words.
"""

NOTE = """<system-reminder>
This is a fresh session. The previous session left this handoff note \
(also saved in {path}):

{note}
</system-reminder>
"""


def meter(request: dict) -> list[tuple[str, int]]:
    """What fills a request, largest first, in estimated tokens."""
    parts = breakdown(request)
    return sorted(((k, v) for k, v in parts.items() if v), key=lambda p: -p[1])


def zone(used: int, limit: int) -> str:
    share = used / limit
    if share < SMART:
        return "smart zone"
    if share < DUMB:
        return "caution: consider /compact or /handoff soon"
    return "dumb zone: compact, hand off or start fresh"


def compact_instructions(root: Path) -> str:
    """The '## Compact instructions' section of AGENTS.md, if the project has one."""
    file = root / "AGENTS.md"
    if not file.is_file():
        return ""
    lines, keep = [], False
    for line in file.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            keep = line[3:].strip().lower() == "compact instructions"
            continue
        if keep:
            lines.append(line)
    text = "\n".join(lines).strip()
    return f"\nProject instructions for this summary:\n{text}\n" if text else ""
