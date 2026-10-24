"""minicode v0.8: agent skills with progressive disclosure.

A skill is a folder with a SKILL.md file (and optional scripts/ and references/):

    .agents/skills/verify-change/
        SKILL.md            front matter (name, description) + instructions
        scripts/smoke.py    code the instructions tell the agent to run
        references/...      extra detail, read only when needed

Level 1: every skill's name and description go into the context.
Level 2: the agent calls load_skill(name) to read the instructions.
Level 3: the agent reads bundled files with read_skill_file(name, path).
"""
from pathlib import Path

from minicode.tools import ToolError

SKILL_DIRS = [Path(".agents") / "skills"]                 # inside the workspace
PERSONAL_DIR = Path.home() / ".minicode" / "skills"       # your own collection


def parse_skill(file: Path) -> dict:
    """Split SKILL.md into front matter (simple key: value lines) and body."""
    text = file.read_text(encoding="utf-8")
    meta, body = {}, text
    if text.startswith("---"):
        header, _, body = text[3:].partition("\n---")
        for line in header.strip().splitlines():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip('"')
    meta.setdefault("name", file.parent.name)
    meta.setdefault("description", "")
    meta["body"] = body.strip()
    meta["folder"] = file.parent
    meta["model_can_invoke"] = meta.get("disable-model-invocation", "false") != "true"
    return meta


class SkillLibrary:
    def __init__(self, root: Path, personal_dir: Path = PERSONAL_DIR):
        self.skills: dict[str, dict] = {}
        dirs = [personal_dir] + [root / d for d in SKILL_DIRS]     # project skills win
        for folder in dirs:
            for file in sorted(folder.glob("*/SKILL.md")) if folder.is_dir() else []:
                skill = parse_skill(file)
                self.skills[skill["name"]] = skill

    def listing(self) -> str:
        """Level 1: names and descriptions only."""
        visible = [s for s in self.skills.values() if s["model_can_invoke"]]
        if not visible:
            return ""
        lines = "\n".join(f"- {s['name']}: {s['description']}" for s in visible)
        return ("<system-reminder>\nSkills you can use. When a task matches a skill's "
                "description, call load_skill(name) first and follow its instructions.\n"
                f"{lines}\n</system-reminder>\n")

    def load_skill(self, name: str) -> str:
        """Load a skill's full instructions by name. Use it when a task matches one of
        the skills listed at the start of the conversation."""
        skill = self.skills.get(name)
        if skill is None or not skill["model_can_invoke"]:
            raise ToolError(f"No skill named '{name}'. Available: {', '.join(self.skills)}.")
        files = sorted(str(p.relative_to(skill["folder"])).replace("\\", "/")
                       for p in skill["folder"].rglob("*")
                       if p.is_file() and p.name != "SKILL.md")
        extra = f"\n\nFiles in this skill (read with read_skill_file): {', '.join(files)}" \
            if files else ""
        return f"Skill folder: {skill['folder']}\n\n{skill['body']}{extra}"

    def read_skill_file(self, name: str, path: str) -> str:
        """Read a file bundled with a skill, e.g. read_skill_file("verify-change",
        "references/endpoints.md")."""
        skill = self.skills.get(name)
        if skill is None:
            raise ToolError(f"No skill named '{name}'.")
        target = (skill["folder"] / path).resolve()
        if skill["folder"].resolve() not in target.parents or not target.is_file():
            raise ToolError(f"'{path}' is not a file in skill '{name}'.")
        return target.read_text(encoding="utf-8")

    def invoke(self, name: str, arguments: str) -> str | None:
        """The user typed /name: the skill's instructions become the request."""
        skill = self.skills.get(name)
        if skill is None:
            return None
        return (f"<system-reminder>\nThe user invoked the skill '{name}'. Skill folder: "
                f"{skill['folder']}\n</system-reminder>\n{skill['body']}\n\n"
                f"Arguments: {arguments.strip() or '(none)'}")
