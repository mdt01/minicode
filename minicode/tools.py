"""The tools minicode can use. Every tool works inside one workspace folder."""
import re
import subprocess
from pathlib import Path
from typing import Callable

MAX_OUTPUT = 10_000          # characters of tool output sent back to the model


class ToolError(Exception):
    """A problem the model should read and fix (bad path, missing text, ...)."""


class Workspace:
    """A folder the agent is allowed to work in, and nothing outside it."""

    def __init__(self, root: str | Path, confirm: Callable[[str], bool] | None = None):
        self.root = Path(root).resolve()
        self.confirm = confirm or ask_user
        self.sandbox = None                 # a security.Sandbox; None = plain subprocess
        self.preapproved = False            # set by the permission policy for one call

    def resolve(self, path: str) -> Path:
        """Turn a relative path into an absolute one, refusing to leave the workspace."""
        target = (self.root / path).resolve()
        if target != self.root and self.root not in target.parents:
            raise ToolError(f"Path '{path}' is outside the workspace. Use paths relative to {self.root}.")
        return target

    # ---- the tools ---------------------------------------------------------------
    def read_file(self, path: str) -> str:
        """Read a text file and return its contents with line numbers.

        path: file path relative to the workspace, e.g. "app/main.py".
        """
        file = self.resolve(path)
        if not file.is_file():
            raise ToolError(f"No file at '{path}'. Use list_files to see what exists.")
        lines = file.read_text(encoding="utf-8").splitlines()
        return "\n".join(f"{i:4} | {line}" for i, line in enumerate(lines, 1))[:MAX_OUTPUT]

    def list_files(self, path: str = ".") -> str:
        """List files and folders in a directory (one level), marking folders with '/'.

        path: directory relative to the workspace; "." means the workspace root.
        """
        folder = self.resolve(path)
        if not folder.is_dir():
            raise ToolError(f"'{path}' is not a directory.")
        names = sorted(
            p.name + ("/" if p.is_dir() else "")
            for p in folder.iterdir()
            if not p.name.startswith(".") and p.name not in {"__pycache__", "node_modules"}
        )
        return "\n".join(names) or "(empty directory)"

    def edit_file(self, path: str, old_text: str, new_text: str) -> str:
        """Replace one exact occurrence of old_text with new_text in a file.

        To create a new file, pass an empty old_text and the full content as new_text.
        old_text must appear exactly once; include a few surrounding lines to make it unique.
        """
        file = self.resolve(path)
        if old_text == "":
            if file.exists():
                raise ToolError(f"'{path}' already exists. Pass the text to replace as old_text.")
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(new_text, encoding="utf-8")
            return f"Created {path} ({len(new_text.splitlines())} lines)."
        if not file.is_file():
            raise ToolError(f"No file at '{path}'.")
        content = file.read_text(encoding="utf-8")
        count = content.count(old_text)
        if count == 0:
            raise ToolError("old_text was not found. Read the file again and copy the text exactly.")
        if count > 1:
            raise ToolError(f"old_text appears {count} times. Include more surrounding lines so it is unique.")
        file.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return f"Edited {path}."

    def run_bash(self, command: str) -> str:
        """Run a shell command in the workspace and return its output (stdout and stderr).

        Use it to run tests (e.g. "pytest -q"), search ("grep -rn seats app"), or inspect git.
        Commands time out after 60 seconds. Risky commands need the user's approval.
        """
        check_command(command)
        if is_risky(command) and not self.preapproved and not self.confirm(command):
            raise ToolError("The user declined this command. Try a safer approach or ask the user.")
        try:
            if self.sandbox:
                done = self.sandbox.run(command, self.root)
            else:
                done = subprocess.run(command, shell=True, cwd=self.root, capture_output=True,
                                      text=True, timeout=60)
        except subprocess.TimeoutExpired:
            raise ToolError("Command timed out after 60 seconds.")
        output = (done.stdout + done.stderr).strip() or "(no output)"
        return f"exit code {done.returncode}\n{output[:MAX_OUTPUT]}"

    def tools(self) -> dict[str, Callable]:
        return {"read_file": self.read_file, "list_files": self.list_files,
                "edit_file": self.edit_file, "run_bash": self.run_bash}


# ---- guardrails for the shell ------------------------------------------------------
BLOCKED = [r"\brm\s+-rf\s+/(\s|$)", r"\bmkfs\b", r"\bshutdown\b", r"\breboot\b", r":\(\)\s*\{",
           r"\bcurl\b[^|]*\|\s*(ba)?sh", r"\bwget\b[^|]*\|\s*(ba)?sh", r"\bdd\s+if=", r"git\s+push\s+.*--force"]
RISKY = [r"\brm\b", r"\bmv\b", r"\bgit\s+(push|reset|checkout|clean)\b", r"\bpip\s+install\b",
         r"\bnpm\s+install\b", r"\bchmod\b", r"\bsudo\b", r">\s*\S"]


def check_command(command: str) -> None:
    for pattern in BLOCKED:
        if re.search(pattern, command):
            raise ToolError(f"Blocked: '{command}' matches a dangerous pattern and will never be run.")


def is_risky(command: str) -> bool:
    return any(re.search(p, command) for p in RISKY)


def ask_user(command: str) -> bool:
    answer = input(f"\n  minicode wants to run:  {command}\n  Allow? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}
