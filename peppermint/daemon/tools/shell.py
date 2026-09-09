"""Shell command tool."""

from __future__ import annotations

import os
import signal
import subprocess
import re
from pathlib import Path

from peppermint import config
from peppermint.daemon import safety
from peppermint.daemon.tools.registry import Confirm, Context, ToolError, tool, truncate

SAFE_ENV_NOTE = "Peppermint runs the command with your normal user account."


def _output(stdout: str, stderr: str) -> str:
    parts = []
    if stdout.strip():
        parts.append(truncate(stdout.strip()))
    if stderr.strip():
        parts.append("[stderr] " + truncate(stderr.strip(), 1000))
    return "\n".join(parts)


@tool(
    name="run_shell",
    description=(
        "Run one shell command and get its output. Use this to inspect the system. "
        "Every command needs approval from the user, including read-only commands. "
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

    # Active swap must never be reformatted or overwritten, even after approval.
    if re.search(r'\b(?:fallocate|dd|mkswap|truncate)\b', cmd):
        try:
            active = [line.split()[0] for line in Path('/proc/swaps').read_text().splitlines()[1:]]
        except OSError:
            raise ToolError('Cannot check active swap. No swap write was attempted.')
        if any(path in cmd for path in active):
            raise ToolError('This command targets active swap. No command was run. '
                            'Do not retry with dd, fallocate or mkswap. Inspect swapon --show and '
                            'available disk space first; propose a separate new swap file or stop.')

    timeout = int(timeout or config.SHELL_TIMEOUT_S)
    timeout = max(1, min(timeout, 300))
    # A separate process group lets a timeout stop the command's children too.
    # Killing only bash can leave its actual work running after we report failure.
    with subprocess.Popen(
        ["/bin/bash", "-lc", cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = proc.communicate(timeout=1)
            except subprocess.TimeoutExpired as exc:
                # A command can deliberately detach a child into another session.
                # Do not wait forever for such a child to close inherited pipes.
                def decoded(value):
                    return value.decode(errors="replace") if isinstance(value, bytes) else value or ""

                stdout, stderr = decoded(exc.stdout), decoded(exc.stderr)
                proc.stdout.close()
                proc.stderr.close()
            output = _output(stdout, stderr)
            raise ToolError(
                f"The command did not finish in {timeout} seconds. "
                "Its process group was stopped; partial changes may remain."
                + (f"\n{output}" if output else "")
            ) from None

    output = _output(stdout, stderr)
    if proc.returncode != 0:
        raise ToolError(
            f"The command failed with exit code {proc.returncode}. Partial changes may remain."
            + (f"\n{output}" if output else "")
        )
    return output or "[the command produced no output]"
