"""The agent loop.

One task moves through this loop:

    idea -> model -> tool call -> tool result -> model -> ... -> answer

A risky tool stops the loop. The task waits for the user. When the user
answers, the loop starts again from the stored message list.
"""

from __future__ import annotations

import json
import logging
import platform
import re
import time
from dataclasses import dataclass
from pathlib import Path

from minty import config
from minty.common.models import Status
from minty.daemon import approval, tools
from minty.daemon.llm import LLM, LLMError
from minty.daemon.tools.registry import Ask, Confirm, Context, ToolError

log = logging.getLogger("minty.agent")

HOME = str(Path.home())


def system_prompt() -> str:
    try:
        info = Path("/etc/linuxmint/info").read_text()
        release = next((ln.split("=", 1)[1].strip('"') for ln in info.splitlines()
                        if ln.startswith("DESCRIPTION")), "Linux Mint")
    except OSError:
        release = "Linux Mint"

    return f"""You are Minty, a helper that lives on this computer.

THIS COMPUTER
- System: {release}, desktop Cinnamon, kernel {platform.release()}.
- The home directory is exactly {HOME}. Always write this path in full.
  Never invent a placeholder path.
- The shell is bash. The package manager is apt.

YOUR JOB
The user gives you an idea. You make it real. You work alone in the background.
Give a short, clear answer at the end. Say what you changed.

RULES
1. Stay on the task. Call only a tool that moves this idea forward. Never call
   a tool that has nothing to do with the idea.
2. Look before you act. Read the current value before you change it.
3. Use the tool that fits. Use gsettings_set for a desktop setting. Use
   open_url to show the user a web site. Use run_shell only when no other tool
   fits. Never open a web page with run_shell.
4. Never guess a path, a package name, or a setting key. Find it with a tool.
5. Some actions need approval from the user. You do not decide this; the system
   decides it and asks for you. Continue after the user answers.
6. Ask a question with ask_user only when you cannot continue without the answer.
7. Work in small steps. One tool call at a time.
8. Never announce what you will do next. Do it first. Text with no tool call
   ends the task, so write text only when all the work is complete.
9. Check your work before you report it. List the folder again, or read the
   setting again. If something is missing, do it now. Never report work you
   did not check.
10. When the work is done, write a short summary with no tool call. Say what
   you changed and where. Two or three sentences is enough.

STYLE
Write short sentences. Use simple words. Do not use markdown headings.
"""


# A small model often stops early and describes work it has not done yet.
# These openings mark a promise, not a result.
PROMISE = re.compile(
    r"\b(i'?ll|i will|i am going to|i'?m going to|let me|next,? i|now,? i'?ll|"
    r"now,? i will|then i will|i need to|i should|first,? i)\b",
    re.IGNORECASE,
)
CONTINUE_NUDGE = (
    "You described work you have not done. Do not describe. Call the tool now. "
    "Write text only when every part of the task is complete."
)


def promises_more(text: str) -> bool:
    """True when the text announces future work instead of reporting it."""
    return bool(PROMISE.search(text))


def attempt_key(tool: str, args: dict) -> str:
    """One name for one exact call, whatever order the arguments arrived in."""
    return f"{tool}:{approval.canonical_args(args)}"


LOOP_WARNING = (
    "You already made this exact call {count} times in this task. It did not "
    "solve the problem. Minty did not run it again. Do something different, or "
    "stop and tell the user why the task cannot finish.\n"
    "What happened last time: {last}"
)


@dataclass
class LoopResult:
    status: Status
    text: str = ""
    confirm: Confirm | None = None
    tool_name: str = ""
    tool_args: dict | None = None


class Agent:
    """Runs one task at a time against the model."""

    def __init__(self, db, llm: LLM | None = None, on_update=None):
        self.db = db
        self.llm = llm or LLM()
        self.on_update = on_update or (lambda task_id, status: None)

    # --- message helpers ---------------------------------------------------

    def _history(self, task_id: int) -> list[dict]:
        messages = self.db.get_messages(task_id)
        if not messages:
            task = self.db.get_task(task_id, with_steps=False)
            messages = [
                {"role": "system", "content": system_prompt()},
                {"role": "user", "content": task.idea},
            ]
            for message in messages:
                self.db.add_message(task_id, message["role"], message)
        return messages

    def _append(self, task_id: int, message: dict) -> None:
        self.db.add_message(task_id, message.get("role", "assistant"), message)

    @staticmethod
    def _to_dict(message) -> dict:
        """Turn an Ollama message into a plain dict we can store."""
        out: dict = {"role": getattr(message, "role", "assistant") or "assistant"}
        content = getattr(message, "content", "") or ""
        out["content"] = content
        calls = getattr(message, "tool_calls", None) or []
        if calls:
            out["tool_calls"] = [
                {"function": {"name": c.function.name,
                              "arguments": dict(c.function.arguments or {})}}
                for c in calls
            ]
        return out

    # --- the loop ----------------------------------------------------------

    def run(self, task_id: int) -> LoopResult:
        """Run the task until it finishes or it needs the user."""
        messages = self._history(task_id)
        deadline = time.time() + config.TASK_TIMEOUT_S
        schemas = tools.schemas()
        repairs = 0
        empty_turns = 0
        nudges = 0
        # Count what this task already tried. The count comes from storage, so
        # it survives an approval, a follow-up, and a restart.
        attempts = self._past_attempts(task_id)

        self.db.set_status(task_id, Status.RUNNING)
        self.on_update(task_id, Status.RUNNING)

        for iteration in range(config.MAX_ITERATIONS):
            if time.time() > deadline:
                return self._fail(task_id, f"The task passed the time limit of {config.TASK_TIMEOUT_S} seconds.")

            task = self.db.get_task(task_id, with_steps=False)
            if task is None or Status(task.status) is Status.CANCELLED:
                return LoopResult(Status.CANCELLED, "You stopped this task.")

            try:
                reply = self.llm.chat(messages, schemas)
            except LLMError as exc:
                return self._fail(task_id, str(exc))

            message = self._to_dict(reply)
            messages.append(message)
            self._append(task_id, message)

            calls = message.get("tool_calls") or []
            text = (message.get("content") or "").strip()

            if not calls:
                if text and promises_more(text) and nudges < config.MAX_CONTINUE_NUDGES:
                    nudges += 1
                    log.info("Task %s stopped with a promise. Minty pushes it on.", task_id)
                    nudge = {"role": "user", "content": CONTINUE_NUDGE}
                    messages.append(nudge)
                    self._append(task_id, nudge)
                    continue
                if text:
                    return self._finish(task_id, text)
                empty_turns += 1
                if empty_turns >= 2:
                    return self._fail(task_id, "The model stopped without an answer.")
                nudge = {"role": "user",
                         "content": "Continue. Call a tool, or write the final summary."}
                messages.append(nudge)
                self._append(task_id, nudge)
                continue

            empty_turns = 0
            for call in calls:
                name = call["function"]["name"]
                args = call["function"].get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}

                key = attempt_key(name, args)
                attempts[key] = attempts.get(key, 0) + 1
                if attempts[key] > config.MAX_SAME_CALL:
                    if attempts[key] > config.MAX_SAME_CALL + 1:
                        return self._fail(task_id, (
                            f"Minty stopped. The model asked for the same action "
                            f"{attempts[key]} times and it never worked: {name}. "
                            "Nothing was run again."))
                    log.info("Task %s: blocked a repeated call to %s", task_id, name)
                    warning = LOOP_WARNING.format(
                        count=attempts[key] - 1,
                        last=self._last_output(task_id, key) or "it failed.")
                    self.db.add_step(task_id, name, args, "safe", warning, "blocked")
                    self.on_update(task_id, Status.RUNNING)
                    tool_message = {"role": "tool", "name": name,
                                    "content": tools.truncate(warning)}
                    messages.append(tool_message)
                    self._append(task_id, tool_message)
                    continue

                outcome = self._execute(task_id, name, args, approved=False)

                if isinstance(outcome, Confirm):
                    return self._pause_for_confirm(task_id, name, args, outcome)
                if isinstance(outcome, Ask):
                    return self._pause_for_answer(task_id, outcome.question)
                if isinstance(outcome, ToolError):
                    repairs += 1
                    if repairs > config.MAX_REPAIRS:
                        return self._fail(task_id, f"The model could not use the tools correctly: {outcome}")
                    result = f"Error: {outcome} Fix the call and try again."
                else:
                    result = str(outcome)

                tool_message = {"role": "tool", "name": name, "content": tools.truncate(result)}
                messages.append(tool_message)
                self._append(task_id, tool_message)

        return self._fail(task_id, f"The task used all {config.MAX_ITERATIONS} steps and did not finish.")

    # --- repeated calls ----------------------------------------------------

    def _past_attempts(self, task_id: int) -> dict[str, int]:
        counts: dict[str, int] = {}
        for step in self.db.get_steps(task_id):
            if step.status == "blocked":
                continue
            key = attempt_key(step.tool, step.args)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _last_output(self, task_id: int, key: str) -> str:
        for step in reversed(self.db.get_steps(task_id)):
            if step.status != "blocked" and attempt_key(step.tool, step.args) == key:
                return tools.truncate(step.output or "", 400)
        return ""

    # --- one tool call -----------------------------------------------------

    def _execute(self, task_id: int, name: str, args: dict, approved: bool,
                 step_id: int = 0):
        """Run one tool. With `step_id`, update that step instead of adding one."""
        ctx = Context(task_id=task_id, db=self.db, approved=approved)
        risk = "risky" if approved else "safe"

        def record(output: str, status: str) -> None:
            if step_id:
                self.db.update_step(step_id, output, status)
            else:
                self.db.add_step(task_id, name, args, risk, output, status)

        # An approved action changes something. Write down that it started,
        # so a crash in the middle is visible afterwards instead of silent.
        if approved and step_id:
            self.db.update_step(step_id, "Minty is doing this now.", "executing")

        try:
            outcome = tools.call(name, args, ctx)
        except ToolError as exc:
            record(str(exc), "error")
            self.on_update(task_id, Status.RUNNING)
            return exc

        if isinstance(outcome, Confirm):
            return outcome
        if isinstance(outcome, Ask):
            record(outcome.question, "asked")
            return outcome

        record(tools.truncate(str(outcome), 2000), "ok")
        self.on_update(task_id, Status.RUNNING)
        return outcome

    # --- resume after the user answers -------------------------------------

    def resume_after_confirm(self, task_id: int, approved: bool) -> LoopResult:
        row = self.db.pending_confirmation_row(task_id)
        if row is None:
            # Nothing waits. Another window probably answered first.
            log.info("Task %s had no pending approval to answer.", task_id)
            return self.run(task_id)

        pending = self.db.pending_confirmation(task_id)
        step = next((s for s in self.db.get_steps(task_id) if s.id == pending.step_id), None)
        if step is None:
            return self._fail(task_id, "Minty lost the action this approval belonged to.")

        # Only the first answer counts. A second window gets nothing.
        if not self.db.resolve_confirmation(pending.id, approved):
            log.info("Task %s: this approval was already answered.", task_id)
            return LoopResult(Status(self.db.get_task(task_id, with_steps=False).status))

        if approved:
            try:
                approval.verify(row, step.tool, step.args, task_id)
            except approval.ApprovalError as exc:
                self.db.update_step(pending.step_id, str(exc), "refused")
                self._append(task_id, {
                    "role": "tool", "name": step.tool,
                    "content": f"{exc} Nothing changed on the computer.",
                })
                log.warning("Task %s: approval refused: %s", task_id, exc)
                return self.run(task_id)

        if not approved:
            self.db.update_step(pending.step_id, "You did not allow this action.", "denied")
            message = {"role": "tool",
                       "name": step.tool if step else "unknown",
                       "content": "The user did not allow this action. Do not try it again. "
                                  "Find another way, or explain why the task cannot finish."}
            self._append(task_id, message)
            return self.run(task_id)

        outcome = self._execute(task_id, step.tool, step.args, approved=True,
                                step_id=pending.step_id)
        if isinstance(outcome, Confirm):
            # A tool asked twice. Treat this as a fault, not a loop.
            content = "The action still needs approval. Stop and tell the user."
            self.db.update_step(pending.step_id, content, "error")
        elif isinstance(outcome, ToolError):
            content = f"Error: {outcome}"
        else:
            content = str(outcome)

        self._append(task_id, {"role": "tool", "name": step.tool, "content": tools.truncate(content)})
        return self.run(task_id)

    def resume_after_answer(self, task_id: int, text: str) -> LoopResult:
        self._append(task_id, {"role": "tool", "name": "ask_user", "content": f"The user answered: {text}"})
        self.db.set_status(task_id, Status.QUEUED, question="")
        return self.run(task_id)

    def follow_up(self, task_id: int, text: str) -> LoopResult:
        self._append(task_id, {"role": "user", "content": text})
        return self.run(task_id)

    # --- end states --------------------------------------------------------

    def _pause_for_confirm(self, task_id: int, name: str, args: dict, confirm: Confirm) -> LoopResult:
        step_id = self.db.add_step(task_id, name, args, "risky", confirm.description,
                                   "pending", mutating=True)
        # The approval is bound to this exact call and to the target as it is
        # right now. Both are checked again before anything runs.
        self.db.add_confirmation(
            task_id, step_id, confirm.description, confirm.reason,
            token=approval.make_token(task_id, name, args),
            expires_at=approval.expiry_time(),
            fingerprint=approval.target_fingerprint(args),
        )
        self.db.set_status(task_id, Status.AWAITING_CONFIRMATION)
        self.on_update(task_id, Status.AWAITING_CONFIRMATION)
        return LoopResult(Status.AWAITING_CONFIRMATION, confirm.description, confirm, name, args)

    def _pause_for_answer(self, task_id: int, question: str) -> LoopResult:
        self.db.set_status(task_id, Status.AWAITING_INPUT, question=question)
        self.on_update(task_id, Status.AWAITING_INPUT)
        return LoopResult(Status.AWAITING_INPUT, question)

    def _finish(self, task_id: int, text: str) -> LoopResult:
        self.db.set_status(task_id, Status.DONE, result=text)
        self.on_update(task_id, Status.DONE)
        return LoopResult(Status.DONE, text)

    def _fail(self, task_id: int, error: str) -> LoopResult:
        log.warning("Task %s failed: %s", task_id, error)
        self.db.set_status(task_id, Status.FAILED, error=error)
        self.on_update(task_id, Status.FAILED)
        return LoopResult(Status.FAILED, error)
