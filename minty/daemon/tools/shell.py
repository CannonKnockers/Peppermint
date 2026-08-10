"""Shell command tool."""

from __future__ import annotations

import subprocess

from minty import config
from minty.daemon import safety
from minty.daemon.tools.registry import Confirm, Context, ToolError, tool, truncate

SAFE_ENV_NOTE = "Minty runs the command with your normal user account."


@tool(
    name="run_shell",
    description=(
        "Run one shell command and get its output. Use this to inspect the system. "
        "Read-only commands run at once. Any other command needs approval from the user. "
        "Prefer a dedicated tool (read_file, gsettings_set, apt_install) when one exists."
    ),
    parameters={
        "type": "object",
        "properties": {
            "cmd": {"type": "string", "description": "The command to run."},
            "purpose": {
                "type": "string",
                "description": "One short sentence that tells the user why you run this command.",
            },
            "timeout": {
                "type": "integer",
                "description": f"Seconds to wait. Default {config.SHELL_TIMEOUT_S}.",
            },
        },
        "required": ["cmd", "purpose"],
    },
)
def run_shell(cmd: str, purpose: str = "", timeout: int | None = None, ctx: Context = None):
    verdict = safety.classify_command(cmd)
    if not verdict.safe and not (ctx and ctx.approved):
        return Confirm(description=f"Run: {cmd}", reason=verdict.reason)

    timeout = int(timeout or config.SHELL_TIMEOUT_S)
    timeout = max(1, min(timeout, 300))
    try:
        proc = subprocess.run(
            ["/bin/bash", "-lc", cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"The command did not finish in {timeout} seconds.")

    parts = []
    if proc.stdout.strip():
        parts.append(truncate(proc.stdout.strip()))
    if proc.stderr.strip():
        parts.append("[stderr] " + truncate(proc.stderr.strip(), 1000))
    if proc.returncode != 0:
        parts.append(f"[exit code {proc.returncode}]")
    return "\n".join(parts) if parts else "[the command produced no output]"
