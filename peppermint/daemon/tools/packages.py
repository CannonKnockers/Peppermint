"""Package tools: search for software and install it."""

from __future__ import annotations

import shutil
import subprocess

from peppermint.daemon.tools.registry import Confirm, Context, ToolError, tool, truncate

TIMEOUT = 60


@tool(
    name="apt_query",
    description=(
        "Find a package or check if a package is installed. Use this before you propose "
        "an install, so you know the correct package name."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The package name or a search word."},
            "action": {
                "type": "string",
                "description": "One of: search, show, installed. Default is search.",
            },
        },
        "required": ["name"],
    },
)
def apt_query(name: str, action: str = "search"):
    action = (action or "search").lower().strip()
    if action == "installed":
        proc = subprocess.run(["dpkg-query", "-W", "-f=${Package} ${Version} ${Status}\n", name],
                              capture_output=True, text=True, timeout=TIMEOUT)
        if proc.returncode != 0 or not proc.stdout.strip():
            return f"The package `{name}` is not installed."
        return proc.stdout.strip()
    if action == "show":
        proc = subprocess.run(["apt-cache", "show", name], capture_output=True, text=True, timeout=TIMEOUT)
        if proc.returncode != 0:
            return f"There is no package named `{name}`."
        keep = ("Package:", "Version:", "Description", "Homepage:", "Installed-Size:")
        lines = [ln for ln in proc.stdout.splitlines() if ln.startswith(keep)]
        return truncate("\n".join(lines[:20]))

    proc = subprocess.run(["apt-cache", "search", "--names-only", name],
                          capture_output=True, text=True, timeout=TIMEOUT)
    if not proc.stdout.strip():
        return f"No package name contains `{name}`."
    return truncate("\n".join(proc.stdout.strip().splitlines()[:30]))


@tool(
    name="apt_install",
    description=(
        "Install one or more packages with apt. This always needs approval from the user. "
        "A password dialog opens on the desktop."
    ),
    parameters={
        "type": "object",
        "properties": {
            "packages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The package names.",
            },
            "why": {"type": "string", "description": "One short sentence for the user."},
        },
        "required": ["packages"],
    },
)
def apt_install(packages, why: str = "", ctx: Context = None):
    if isinstance(packages, str):
        packages = [p for p in packages.replace(",", " ").split() if p]
    if not packages:
        raise ToolError("Give at least one package name.")
    for name in packages:
        if not all(c.isalnum() or c in ".+-_" for c in name):
            raise ToolError(f"`{name}` is not a valid package name.")

    if not (ctx and ctx.approved):
        detail = f"Install: {', '.join(packages)}"
        return Confirm(description=detail, reason=why or "an install changes system software")

    if not shutil.which("pkexec"):
        raise ToolError("The `pkexec` command is missing, so Peppermint cannot ask for a password.")

    proc = subprocess.run(
        ["pkexec", "apt-get", "install", "-y", *packages],
        capture_output=True, text=True, timeout=600,
        env={"DEBIAN_FRONTEND": "noninteractive", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
    )
    if proc.returncode == 126:
        raise ToolError("You closed the password dialog, so nothing was installed.")
    if proc.returncode != 0:
        raise ToolError(f"The install failed: {truncate(proc.stderr.strip(), 800)}")
    return f"Installed: {', '.join(packages)}.\n{truncate(proc.stdout.strip(), 1000)}"


@tool(
    name="list_apps",
    description="List the applications that have a menu entry, with the command each one runs.",
    parameters={
        "type": "object",
        "properties": {"search": {"type": "string", "description": "Show only names that contain this text."}},
    },
)
def list_apps(search: str = ""):
    cmd = (
        "grep -h -m1 '^Name=' /usr/share/applications/*.desktop "
        "~/.local/share/applications/*.desktop 2>/dev/null | sed 's/^Name=//' | sort -u"
    )
    proc = subprocess.run(["/bin/bash", "-lc", cmd], capture_output=True, text=True, timeout=TIMEOUT)
    names = [n for n in proc.stdout.splitlines() if n.strip()]
    if search:
        names = [n for n in names if search.lower() in n.lower()]
    if not names:
        return "Peppermint found no matching application."
    return f"{len(names)} applications:\n" + truncate("\n".join(names[:200]))
