"""minicode v0.9: the agent loop with plan mode, context, MCP, skills, hooks and subagents."""
import json
import os
from typing import Callable

from openai import OpenAI

from minicode.context import HANDOFF_PROMPT, NOTE, compact_instructions
from minicode.compaction import COMPACT_PROMPT, RESUME, estimate_tokens, extract_summary
from minicode.prompts import (APPROVED, PLAN_MODE, global_rules, nested_rules,
                              project_rules, system_prompt)
from minicode.subagents import delegate_schema
from minicode.schema import tool_schema
from minicode.tools import ToolError, Workspace
from minicode.toolsearch import ToolCatalog

PLAN_FILE = ".minicode/plan.md"
NOTES_FILE = ".minicode/progress.md"
PLAN_TOOLS = {"read_file", "list_files", "write_plan", "load_skill", "read_skill_file"}   # read-only, plus the plan file


class Agent:
    def __init__(self, client: OpenAI, model: str, workspace: Workspace,
                 max_steps: int = 25, on_event: Callable[[str], None] = print,
                 context_limit: int = 100_000, compact_at: float = 0.7):
        self.client = client
        self.model = model
        self.workspace = workspace
        self.tools = workspace.tools() | {"write_plan": self.write_plan}
        self.schemas: dict[str, dict] = {}          # ready-made schemas (MCP tools)
        self.catalog: ToolCatalog | None = None     # deferred tools (tool search mode)
        self.max_steps = max_steps
        self.on_event = on_event
        self.context_limit = context_limit
        self.compact_at = compact_at
        self.mode = "normal"                                # or "plan"
        self.tokens = {"input": 0, "output": 0}
        self.last_input = 0                                 # size of the last request
        self.rules_sent = False
        self.pending_note = ""                             # handoff note for a fresh start
        self.skills = None                                  # a SkillLibrary, if any
        self.hooks = None                                   # Hooks from .agents/hooks.json
        self.subagents: dict[str, dict] = {}                # definitions, .agents/agents
        self.loaded_rules: set = set()                      # nested AGENTS.md already sent
        self.changed: set[str] = set()                      # files edited this turn
        self.messages: list[dict] = [
            {"role": "system", "content": system_prompt(workspace.root)}]

    # ---- the loop ------------------------------------------------------------------
    def run_turn(self, user_text: str) -> str:
        """Handle one user request: loop until the model stops asking for tools."""
        self.messages.append({"role": "user", "content": self.reminders() + user_text})
        self.changed = set()
        stop_checks = 0
        for _ in range(self.max_steps):
            if len(self.messages) > 2 and self.context_used() > self.context_limit * self.compact_at:
                self.compact()                              # automatic compaction
            tools = self.active_tools()
            response = self.client.chat.completions.create(
                model=self.model, messages=self.messages, tools=self.schemas_for(tools))
            self.count(response)
            message = response.choices[0].message
            self.messages.append(message.model_dump(exclude_none=True))
            if not message.tool_calls:
                answer = message.content or ""
                if self.hooks and stop_checks < 3:          # may the agent stop now?
                    blocked, feedback = self.hooks.run("stop", {
                        "changed_files": sorted(self.changed), "answer": answer})
                    if blocked:
                        stop_checks += 1
                        self.on_event("  [stop hook: not finished yet]")
                        self.messages.append({"role": "user", "content": (
                            "<system-reminder>\nA project hook says the work is not "
                            f"finished:\n{feedback}\nFix this, then finish again."
                            "\n</system-reminder>")})
                        continue
                return answer
            for call in message.tool_calls:
                result = self.execute(call.function.name, call.function.arguments, tools)
                self.messages.append({"role": "tool", "tool_call_id": call.id,
                                      "content": result})
        return "Stopped: reached the step limit. Ask me to continue if needed."

    def reminders(self) -> str:
        """Text the harness adds in front of the user's message."""
        text = ""
        if not self.rules_sent:                             # rules, once
            text += global_rules() + project_rules(self.workspace.root)
            if self.skills:                                 # level 1: skill names
                text += self.skills.listing()
            self.rules_sent = True
        if self.pending_note:                               # after /handoff, once
            text += self.pending_note
            self.pending_note = ""
        if self.mode == "plan":
            text += PLAN_MODE
        return text

    def active_tools(self) -> dict[str, Callable]:
        if self.mode == "plan":
            return {n: t for n, t in self.tools.items() if n in PLAN_TOOLS}
        return {n: t for n, t in self.tools.items() if n != "write_plan"}

    def execute(self, name: str, arguments: str, tools: dict | None = None) -> str:
        """Run one tool safely. Errors go back to the model as text it can act on."""
        self.on_event(f"  -> {name}({arguments[:80]})")
        tools = self.active_tools() if tools is None else tools
        tool = tools.get(name)
        if tool is None:
            return f"Error: tool '{name}' is not available now. Available: {', '.join(tools)}."
        try:
            args = json.loads(arguments or "{}")
            if self.hooks:                                  # pre-tool hooks may block
                blocked, feedback = self.hooks.run("pre_tool", {"tool": name, "arguments": args})
                if blocked:
                    return f"Error: blocked by a project hook: {feedback}"
            result = str(tool(**args))
            if name == "edit_file":
                self.changed.add(args.get("path", ""))
            if self.hooks:                                  # post-tool hooks give feedback
                _, feedback = self.hooks.run("post_tool", {"tool": name, "arguments": args,
                                                           "result": result[:2000]})
                if feedback:
                    result += f"\n<system-reminder>\nHook feedback:\n{feedback}\n</system-reminder>"
            if name in ("read_file", "list_files", "edit_file") and "path" in args:
                result += nested_rules(self.workspace.root, args["path"], self.loaded_rules)
            return result
        except json.JSONDecodeError:
            return "Error: arguments were not valid JSON."
        except TypeError as exc:
            return f"Error: wrong arguments for {name}: {exc}"
        except ToolError as exc:
            return f"Error: {exc}"

    def count(self, response) -> None:
        if response.usage:
            self.tokens["input"] += response.usage.prompt_tokens
            self.tokens["output"] += response.usage.completion_tokens
            self.last_input = response.usage.prompt_tokens

    # ---- plan mode -----------------------------------------------------------------
    def write_plan(self, content: str) -> str:
        """Write the implementation plan (Markdown) to the plan file. Plan mode only.

        content: the complete plan. Calling again replaces the previous version.
        """
        file = self.workspace.resolve(PLAN_FILE)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
        return f"Plan saved to {PLAN_FILE}. Ask the user to review it and type /go."

    def approve_plan(self) -> str:
        """The user typed /go: leave plan mode and implement the saved plan."""
        file = self.workspace.resolve(PLAN_FILE)
        if not file.is_file():
            return "There is no plan yet. Ask for one in plan mode first (/plan)."
        self.mode = "normal"
        return self.run_turn(APPROVED.format(plan=file.read_text(encoding="utf-8")))

    # ---- compaction ----------------------------------------------------------------
    def context_used(self) -> int:
        """Tokens in the next request: the last real count, or an estimate."""
        preview = self.request_preview()
        estimate = estimate_tokens(preview["messages"]) + estimate_tokens(preview["tools"])
        return max(self.last_input, estimate)

    def compact(self, focus: str = "") -> str:
        """Summarize the conversation and start again from the summary."""
        before = self.context_used()
        focus += compact_instructions(self.workspace.root)
        request = self.messages + [{"role": "user", "content": COMPACT_PROMPT + focus}]
        response = self.client.chat.completions.create(model=self.model, messages=request)
        self.count(response)
        summary = extract_summary(response.choices[0].message.content or "")
        skills = self.skills.listing() if self.skills else ""
        resume = project_rules(self.workspace.root) + skills + RESUME.format(summary=summary)
        self.messages = [self.messages[0], {"role": "user", "content": resume}]
        self.loaded_rules = set()                           # nested rules load again
        self.last_input = 0
        after = estimate_tokens(self.messages)
        self.on_event(f"  [compacted: about {before:,} -> {after:,} tokens]")
        return summary


    # ---- clearing and handing off ----------------------------------------------------
    def request_preview(self) -> dict:
        """The request the next model call would send (for the context meter)."""
        return {"messages": self.messages, "tools": self.schemas_for(self.active_tools())}

    # ---- external tools (MCP) -------------------------------------------------------
    def add_tools(self, tools: dict[str, tuple[Callable, dict]], deferred: bool = False) -> None:
        """Add tools that come with their own schema, e.g. from an MCP server.

        deferred=True puts them in a searchable catalog instead of the tool list:
        the model then sees only search_tools and call_tool (tool search mode)."""
        if deferred:
            if self.catalog is None:
                self.catalog = ToolCatalog()
                self.tools["search_tools"] = self.catalog.search_tools
                self.tools["call_tool"] = self.catalog.call_tool
            self.catalog.add(tools)
            return
        for name, (function, schema) in tools.items():
            self.tools[name] = function
            self.schemas[name] = schema

    def add_skills(self, library) -> None:
        """Offer skills: names in the first message, full text through load_skill."""
        if library.skills:
            self.skills = library
            self.tools["load_skill"] = library.load_skill
            self.tools["read_skill_file"] = library.read_skill_file

    # ---- hooks and subagents --------------------------------------------------------
    def add_hooks(self, hooks) -> None:
        if hooks:
            self.hooks = hooks

    def add_subagents(self, definitions: dict[str, dict]) -> None:
        """Offer a delegate tool that runs a subagent in a fresh context."""
        if definitions:
            self.subagents = definitions
            self.tools["delegate"] = self.delegate
            self.schemas["delegate"] = delegate_schema(definitions)

    def delegate(self, agent: str, task: str) -> str:
        definition = self.subagents.get(agent)
        if definition is None:
            raise ToolError(f"No subagent '{agent}'. Available: {', '.join(self.subagents)}.")
        child = Agent(self.client, self.model, self.workspace, max_steps=self.max_steps,
                      on_event=lambda e: self.on_event(f"    [{agent}] {e.strip()}"),
                      context_limit=self.context_limit)
        child.messages[0]["content"] += f"\n# Your role: {agent}\n{definition['body']}\n"
        if self.skills:
            child.add_skills(self.skills)
        child.hooks = self.hooks                            # same rules apply to helpers
        allowed = definition["tools"]
        if allowed:                                         # restrict the helper's tools
            child.tools = {n: t for n, t in child.tools.items() if n in allowed}
        answer = child.run_turn(task)                       # a fresh context
        for key in self.tokens:
            self.tokens[key] += child.tokens[key]
        self.changed |= child.changed
        return f"[{agent} finished]\n{answer}"

    def schemas_for(self, tools: dict[str, Callable]) -> list[dict]:
        return [self.schemas.get(name) or tool_schema(name, fn) for name, fn in tools.items()]

    def clear(self) -> None:
        """Forget the conversation. The system prompt and project rules come back."""
        self.messages = self.messages[:1]
        self.loaded_rules = set()
        self.rules_sent = False
        self.last_input = 0

    def handoff(self) -> str:
        """Write a handoff note to disk, then start a fresh conversation from it."""
        request = self.messages + [{"role": "user", "content": HANDOFF_PROMPT}]
        response = self.client.chat.completions.create(model=self.model, messages=request)
        self.count(response)
        note = (response.choices[0].message.content or "").strip()
        file = self.workspace.resolve(NOTES_FILE)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(note + "\n", encoding="utf-8")
        self.clear()
        self.pending_note = NOTE.format(path=NOTES_FILE, note=note)
        return note


def make_client() -> tuple[OpenAI, str]:
    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    key, model = os.getenv("LLM_API_KEY"), os.getenv("LLM_MODEL")
    if not key or not model:
        raise SystemExit("Set LLM_API_KEY and LLM_MODEL first.")
    return OpenAI(base_url=base_url, api_key=key), model
