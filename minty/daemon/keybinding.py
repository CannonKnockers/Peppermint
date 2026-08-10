"""Cinnamon custom keyboard shortcuts.

Cinnamon keeps custom shortcuts in two places:

* `org.cinnamon.desktop.keybindings custom-list` holds the slot names, for
  example ['custom0', 'custom1'].
* Each slot has its own path, for example
  `/org/cinnamon/desktop/keybindings/custom-keybindings/custom0/`, with the
  keys `name`, `command`, and `binding`.
"""

from __future__ import annotations

import ast
import subprocess

LIST_SCHEMA = "org.cinnamon.desktop.keybindings"
LIST_KEY = "custom-list"
SLOT_SCHEMA = "org.cinnamon.desktop.keybindings.custom-keybinding"
SLOT_PATH = "/org/cinnamon/desktop/keybindings/custom-keybindings/{slot}/"
TIMEOUT = 15


def _run(args: list[str]) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "gsettings failed")
    return proc.stdout.strip()


def _parse(value: str, default):
    try:
        parsed = ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return default
    return parsed


def _slot_get(slot: str, key: str) -> str:
    out = _run(["gsettings", "get", f"{SLOT_SCHEMA}:{SLOT_PATH.format(slot=slot)}", key])
    parsed = _parse(out, out)
    if isinstance(parsed, list):
        return parsed[0] if parsed else ""
    return str(parsed)


def _slot_set(slot: str, key: str, value: str) -> None:
    _run(["gsettings", "set", f"{SLOT_SCHEMA}:{SLOT_PATH.format(slot=slot)}", key, value])


def slots() -> list[str]:
    raw = _run(["gsettings", "get", LIST_SCHEMA, LIST_KEY])
    value = _parse(raw, [])
    if not isinstance(value, list):
        return []
    # Cinnamon writes the placeholder ['__dummy__'] when the list is empty.
    return [s for s in value if s != "__dummy__"]


def list_all() -> list[dict]:
    out = []
    for slot in slots():
        try:
            out.append({
                "slot": slot,
                "name": _slot_get(slot, "name"),
                "command": _slot_get(slot, "command"),
                "binding": _slot_get(slot, "binding"),
            })
        except RuntimeError:
            continue
    return out


def find_by_command(command: str) -> dict | None:
    for entry in list_all():
        if entry["command"].strip() == command.strip():
            return entry
    return None


def install(name: str, command: str, binding: str) -> str:
    """Make or update a shortcut. Returns the slot name."""
    existing = find_by_command(command)
    current = slots()

    if existing:
        slot = existing["slot"]
    else:
        used = {int(s.replace("custom", "")) for s in current if s.startswith("custom") and s[6:].isdigit()}
        index = 0
        while index in used:
            index += 1
        slot = f"custom{index}"

    _slot_set(slot, "name", name)
    _slot_set(slot, "command", command)
    _slot_set(slot, "binding", f"['{binding}']")

    if slot not in current:
        new_list = current + [slot]
        _run(["gsettings", "set", LIST_SCHEMA, LIST_KEY, str(new_list)])
    return slot


def remove(command: str) -> bool:
    entry = find_by_command(command)
    if entry is None:
        return False
    remaining = [s for s in slots() if s != entry["slot"]]
    _run(["gsettings", "set", LIST_SCHEMA, LIST_KEY, str(remaining)])
    subprocess.run(
        ["dconf", "reset", "-f", SLOT_PATH.format(slot=entry["slot"])],
        capture_output=True, text=True, timeout=TIMEOUT,
    )
    return True
