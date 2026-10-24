"""minicode v0.9: hooks - deterministic scripts that run at fixed moments in the loop.

Configured in .agents/hooks.json:

    {"pre_tool":  [{"match": "edit_file", "command": "python .agents/hooks/guard.py"}],
     "post_tool": [{"match": "edit_file", "command": "python .agents/hooks/check_edit.py"}],
     "stop":      [{"command": "python .agents/hooks/test_gate.py"}]}

Each hook receives a JSON description of the event on standard input.
Exit code 0: carry on (anything printed is passed to the model as feedback).
Exit code 2: block - the tool call is refused, or the agent may not stop yet;
             the hook's message (stderr, or stdout) tells the model why.
Any other exit code: the hook itself failed; this is reported but does not block.
"""
import json
import re
import subprocess
from pathlib import Path

HOOK_FILE = Path(".agents") / "hooks.json"
EVENTS = ("pre_tool", "post_tool", "stop")


class Hooks:
    def __init__(self, root: Path):
        self.root = root
        file = root / HOOK_FILE
        self.config = json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {}

    def __bool__(self) -> bool:
        return any(self.config.get(e) for e in EVENTS)

    def run(self, event: str, payload: dict) -> tuple[bool, str]:
        """Run every hook for this event. Returns (blocked, message for the model)."""
        notes = []
        for hook in self.config.get(event, []):
            if not re.fullmatch(hook.get("match", ".*"), payload.get("tool", "")):
                continue
            try:
                done = subprocess.run(hook["command"], shell=True, cwd=self.root,
                                      input=json.dumps({"event": event, **payload}),
                                      capture_output=True, text=True,
                                      timeout=hook.get("timeout", 120))
            except subprocess.TimeoutExpired:
                notes.append(f"Hook '{hook['command']}' timed out.")
                continue
            message = (done.stderr.strip() or done.stdout.strip())
            if done.returncode == 2:
                return True, message or f"Blocked by hook '{hook['command']}'."
            if done.returncode != 0:
                notes.append(f"Hook '{hook['command']}' failed (exit {done.returncode}): "
                             f"{message}")
            elif done.stdout.strip():
                notes.append(done.stdout.strip())
        return False, "\n".join(notes)
