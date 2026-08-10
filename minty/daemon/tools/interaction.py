"""Tools that talk to the user: notifications, questions, and system facts."""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

from minty.daemon.tools.registry import Ask, Context, tool, truncate


@tool(
    name="notify_user",
    description="Show a desktop notification. Use this for a fact the user wants at once.",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["title", "body"],
    },
)
def notify_user(title: str, body: str, ctx: Context = None):
    from minty.daemon import notifier

    notifier.send(title, body)
    return "The notification is on the screen."


@tool(
    name="ask_user",
    description=(
        "Ask the user one question and wait for the answer. Use this only when you cannot "
        "continue without the answer. Do not ask for a fact you can find with a tool."
    ),
    parameters={
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    },
)
def ask_user(question: str):
    return Ask(question=question)


@tool(
    name="system_info",
    description="Get facts about this computer: the distribution, the desktop, the disks, the memory, and the CPU.",
    parameters={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "One of: all, os, disk, memory, cpu, gpu, network, battery.",
            }
        },
    },
)
def system_info(topic: str = "all"):
    topic = (topic or "all").lower().strip()
    parts: list[str] = []

    def want(name: str) -> bool:
        return topic in ("all", name)

    if want("os"):
        info = Path("/etc/linuxmint/info")
        release = info.read_text().strip() if info.exists() else platform.platform()
        parts.append("[os]\n" + "\n".join(
            ln for ln in release.splitlines() if ln.startswith(("DESCRIPTION", "RELEASE", "CODENAME"))
        ) or release)
        parts.append(f"kernel: {platform.release()}")

    if want("disk"):
        proc = subprocess.run(["df", "-h", "--output=source,size,used,avail,pcent,target", "-x", "tmpfs",
                               "-x", "devtmpfs", "-x", "squashfs"],
                              capture_output=True, text=True, timeout=15)
        parts.append("[disk]\n" + proc.stdout.strip())

    if want("memory"):
        proc = subprocess.run(["free", "-h"], capture_output=True, text=True, timeout=15)
        parts.append("[memory]\n" + proc.stdout.strip())

    if want("cpu"):
        proc = subprocess.run(["/bin/bash", "-lc", "lscpu | grep -E 'Model name|^CPU\\(s\\)|MHz'"],
                              capture_output=True, text=True, timeout=15)
        parts.append("[cpu]\n" + proc.stdout.strip())

    if want("gpu") and shutil.which("nvidia-smi"):
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,temperature.gpu", "--format=csv"],
            capture_output=True, text=True, timeout=15)
        parts.append("[gpu]\n" + proc.stdout.strip())

    if want("network"):
        proc = subprocess.run(["/bin/bash", "-lc", "ip -brief addr | grep -v '^lo'"],
                              capture_output=True, text=True, timeout=15)
        parts.append("[network]\n" + proc.stdout.strip())

    if want("battery"):
        proc = subprocess.run(["/bin/bash", "-lc",
                               "upower -i $(upower -e | grep BAT | head -1) 2>/dev/null | "
                               "grep -E 'state|percentage|time to'"],
                              capture_output=True, text=True, timeout=15)
        parts.append("[battery]\n" + (proc.stdout.strip() or "This computer has no battery."))

    if not parts:
        return f"`{topic}` is not a known topic. Use: all, os, disk, memory, cpu, gpu, network, battery."
    return truncate("\n\n".join(parts))
