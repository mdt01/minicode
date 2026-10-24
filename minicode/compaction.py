"""minicode v0.3: compaction - replace a long conversation with a structured summary."""
import json

COMPACT_PROMPT = """Do not call any tools. Reply with plain text only.

Summarize this conversation so that work can continue without it. First think in \
an <analysis> block, then write a <summary> block with these sections:

1. User requests: every request and goal, in detail.
2. Key decisions and technical details.
3. Files: each file read, changed or created, and why. Include short code \
snippets for the most recent changes.
4. Errors and fixes, including any correction the user gave you.
5. User messages: list every message the user wrote. Copy any rule or \
constraint (files to avoid, actions not allowed, secrets) word for word.
6. Pending tasks.
7. Current work: exactly what you were doing just before this summary.
8. Next step: the single next action, only if it follows from the user's \
latest request.
"""

RESUME = """<system-reminder>
The earlier conversation was compacted to save space. This summary replaces it:

{summary}

Continue the work from where it stopped. Do not ask the user to repeat anything.
</system-reminder>"""


def estimate_tokens(messages: list[dict]) -> int:
    """A rough estimate: about four characters per token."""
    return len(json.dumps(messages)) // 4


def extract_summary(text: str) -> str:
    """Keep only the <summary> part; the analysis was just the model's notes."""
    start, end = text.find("<summary>"), text.find("</summary>")
    if start != -1 and end != -1:
        return text[start + len("<summary>"):end].strip()
    return text.strip()
