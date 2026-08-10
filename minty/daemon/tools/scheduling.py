"""Scheduling tools. Minty uses the user crontab."""

from __future__ import annotations

import re
import subprocess

from minty.daemon.tools.registry import Confirm, Context, ToolError, tool

MARKER = "# minty"
TIMEOUT = 20
CRON_FIELD = re.compile(r"^[\d\*/,\-]+$")


def _read_crontab() -> str:
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=TIMEOUT)
    if proc.returncode != 0 and "no crontab" not in proc.stderr.lower():
        raise ToolError(f"Minty could not read the crontab: {proc.stderr.strip()}")
    return proc.stdout


def _write_crontab(text: str) -> None:
    if not text.endswith("\n"):
        text += "\n"
    proc = subprocess.run(["crontab", "-"], input=text, capture_output=True, text=True, timeout=TIMEOUT)
    if proc.returncode != 0:
        raise ToolError(f"Minty could not write the crontab: {proc.stderr.strip()}")


def _valid_cron(expr: str) -> bool:
    fields = expr.split()
    if len(fields) != 5:
        return False
    return all(CRON_FIELD.match(f) for f in fields)


@tool(
    name="list_scheduled",
    description="List the scheduled jobs in the user crontab.",
    parameters={"type": "object", "properties": {}},
)
def list_scheduled():
    text = _read_crontab()
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        return "There is no scheduled job."
    return "schedule\tcommand\n" + "\n".join(lines)


@tool(
    name="schedule",
    description=(
        "Add a job to the user crontab. The schedule uses five cron fields: "
        "minute hour day-of-month month day-of-week. For example '0 9 * * 1' means "
        "every Monday at 09:00. This needs approval from the user."
    ),
    parameters={
        "type": "object",
        "properties": {
            "cron_expr": {"type": "string", "description": "The five cron fields."},
            "command": {"type": "string", "description": "The command to run."},
            "comment": {"type": "string", "description": "A short note about the job."},
        },
        "required": ["cron_expr", "command"],
    },
)
def schedule(cron_expr: str, command: str, comment: str = "", ctx: Context = None):
    cron_expr = cron_expr.strip()
    if not _valid_cron(cron_expr):
        raise ToolError(
            f"`{cron_expr}` is not a valid schedule. Give five fields, "
            "for example '30 8 * * *' for every day at 08:30."
        )
    if "\n" in command:
        raise ToolError("The command must be one line.")

    if not (ctx and ctx.approved):
        return Confirm(
            description=f"Add a scheduled job: `{command}` at `{cron_expr}`",
            reason="a scheduled job runs later without your attention",
        )

    text = _read_crontab()
    note = f" {MARKER}: {comment}" if comment else f" {MARKER}"
    line = f"{cron_expr} {command}{note}"
    if line in text:
        return "That job is already in the crontab."
    _write_crontab(text + line + "\n")
    return f"Added the job. It runs `{command}` at `{cron_expr}`."


@tool(
    name="unschedule",
    description="Remove a scheduled job. Give text that appears in the job line.",
    parameters={
        "type": "object",
        "properties": {"match": {"type": "string", "description": "Text from the job line."}},
        "required": ["match"],
    },
)
def unschedule(match: str, ctx: Context = None):
    text = _read_crontab()
    lines = text.splitlines()
    hits = [ln for ln in lines if match in ln and ln.strip() and not ln.strip().startswith("#")]
    if not hits:
        return f"No scheduled job contains `{match}`."
    if not (ctx and ctx.approved):
        return Confirm(
            description="Remove these jobs:\n" + "\n".join(hits),
            reason="the job stops running after this change",
        )
    kept = [ln for ln in lines if ln not in hits]
    _write_crontab("\n".join(kept))
    return f"Removed {len(hits)} scheduled jobs."
