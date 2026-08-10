"""Desktop settings tools: gsettings and Cinnamon keyboard shortcuts."""

from __future__ import annotations

import subprocess

from peppermint.daemon import safety
from peppermint.daemon.tools.registry import Confirm, Context, ToolError, tool, truncate

TIMEOUT = 15


def _gsettings(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["gsettings", *args], capture_output=True, text=True, timeout=TIMEOUT)


@tool(
    name="gsettings_get",
    description=(
        "Read one desktop setting. Common values: schema `org.cinnamon.desktop.interface` "
        "with key `gtk-theme`, `icon-theme`, `cursor-theme`, or `font-name`; schema "
        "`org.cinnamon.theme` with key `name`; schema `org.cinnamon.desktop.background` "
        "with key `picture-uri`."
    ),
    parameters={
        "type": "object",
        "properties": {
            "schema": {"type": "string"},
            "key": {"type": "string"},
        },
        "required": ["schema", "key"],
    },
)
def gsettings_get(schema: str, key: str):
    proc = _gsettings("get", schema, key)
    if proc.returncode != 0:
        raise ToolError(f"gsettings could not read {schema} {key}: {proc.stderr.strip()}")
    return proc.stdout.strip()


@tool(
    name="gsettings_list",
    description="List the keys of a settings schema, or search all schemas by name. Use this to find the correct key.",
    parameters={
        "type": "object",
        "properties": {
            "schema": {"type": "string", "description": "List the keys of this schema."},
            "search": {"type": "string", "description": "Find schemas whose name contains this text."},
        },
    },
)
def gsettings_list(schema: str = "", search: str = ""):
    if schema:
        proc = _gsettings("list-keys", schema)
        if proc.returncode != 0:
            raise ToolError(f"There is no schema named `{schema}`.")
        return f"Keys of {schema}:\n" + proc.stdout.strip()
    if search:
        proc = _gsettings("list-schemas")
        matches = [s for s in proc.stdout.split() if search.lower() in s.lower()]
        if not matches:
            return f"No schema name contains `{search}`."
        return "\n".join(sorted(matches)[:80])
    raise ToolError("Give either `schema` or `search`.")


@tool(
    name="gsettings_set",
    description=(
        "Change one desktop setting. Peppermint applies a normal desktop setting at once and "
        "keeps the old value, so you can undo it. Any other schema needs approval."
    ),
    parameters={
        "type": "object",
        "properties": {
            "schema": {"type": "string"},
            "key": {"type": "string"},
            "value": {"type": "string", "description": "The new value. Text values need quotes, for example 'Mint-Y'."},
        },
        "required": ["schema", "key", "value"],
    },
)
def gsettings_set(schema: str, key: str, value: str, ctx: Context = None):
    verdict = safety.classify_setting(schema)
    if not verdict.safe and not (ctx and ctx.approved):
        return Confirm(description=f"Set {schema} {key} to {value}", reason=verdict.reason)

    check = _gsettings("get", schema, key)
    if check.returncode != 0:
        raise ToolError(f"There is no setting {schema} {key}.")
    old = check.stdout.strip()

    proc = _gsettings("set", schema, key, value)
    if proc.returncode != 0:
        hint = proc.stderr.strip()
        raise ToolError(
            f"gsettings rejected the value: {hint}. The old value was {old}. "
            "A text value usually needs single quotes."
        )

    if ctx and ctx.db:
        ctx.db.record_undo(ctx.task_id, "gsettings", f"{schema} {key}", old)
    return f"Changed {schema} {key} from {old} to {value}."


@tool(
    name="list_themes",
    description="List the themes that are installed on this computer.",
    parameters={
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "description": "One of: gtk, icon, cursor, cinnamon.",
            }
        },
        "required": ["kind"],
    },
)
def list_themes(kind: str):
    paths = {
        "gtk": ["/usr/share/themes", "~/.themes"],
        "cinnamon": ["/usr/share/themes", "~/.themes"],
        "icon": ["/usr/share/icons", "~/.icons", "~/.local/share/icons"],
        "cursor": ["/usr/share/icons", "~/.icons"],
    }
    kind = kind.lower().strip()
    if kind not in paths:
        raise ToolError(f"`kind` must be one of: {', '.join(paths)}.")

    marker = {"gtk": "gtk-3.0", "cinnamon": "cinnamon", "icon": "index.theme", "cursor": "cursors"}[kind]
    cmd = " ; ".join(
        f"find {p} -maxdepth 2 -name '{marker}' -printf '%h\\n' 2>/dev/null" for p in paths[kind]
    )
    proc = subprocess.run(["/bin/bash", "-lc", cmd], capture_output=True, text=True, timeout=TIMEOUT)
    names = sorted({line.rstrip("/").split("/")[-1] for line in proc.stdout.splitlines() if line.strip()})
    if not names:
        return f"Peppermint found no {kind} theme."
    return f"{len(names)} {kind} themes:\n" + "\n".join(names)


@tool(
    name="set_keybinding",
    description=(
        "Make a custom keyboard shortcut in Cinnamon. Peppermint puts it in the first free slot. "
        "Use a binding like '<Super>space' or '<Primary><Alt>t'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The name shown in the settings window."},
            "command": {"type": "string", "description": "The command to run."},
            "binding": {"type": "string", "description": "The key combination."},
        },
        "required": ["name", "command", "binding"],
    },
)
def set_keybinding(name: str, command: str, binding: str, ctx: Context = None):
    from peppermint.daemon import keybinding

    if not (ctx and ctx.approved):
        return Confirm(
            description=f"Bind {binding} to `{command}` with the name '{name}'",
            reason="a shortcut changes how your keyboard works",
        )
    slot = keybinding.install(name, command, binding)
    return f"Made the shortcut {binding} for `{command}` in slot {slot}."


@tool(
    name="list_keybindings",
    description="List the custom keyboard shortcuts of Cinnamon.",
    parameters={"type": "object", "properties": {}},
)
def list_keybindings():
    from peppermint.daemon import keybinding

    entries = keybinding.list_all()
    if not entries:
        return "There is no custom keyboard shortcut."
    lines = [f"{e['binding']}\t{e['name']}\t{e['command']}" for e in entries]
    return truncate("binding\tname\tcommand\n" + "\n".join(lines))
