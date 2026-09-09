"""Install Cinnamon's recovery shortcut, preserving displaced bindings.

Run with ``--plan`` to inspect, ``--install`` to apply, or ``--restore`` to undo.
Only the per-user desktop keybindings are changed; no root access is required.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
from typing import Any

LIST_SCHEMA = "org.cinnamon.desktop.keybindings"
SLOT_SCHEMA = LIST_SCHEMA + ".custom-keybinding"
SLOT_PATH = "/org/cinnamon/desktop/keybindings/custom-keybindings/{slot}/"
RECOVERY_KEY = "<Control><Alt>Delete"
REPLACEMENT_KEY = "<Control><Alt><Shift>Delete"
RECOVERY_NAME = "Peppermint Recovery"


@dataclass(frozen=True)
class Setting:
    schema: str
    key: str
    path: str = ""

    @property
    def label(self) -> str:
        return f"{self.schema}{':' + self.path if self.path else ''} {self.key}"


@dataclass
class Binding:
    setting: Setting
    name: str
    accelerators: list[str]
    command: str = ""
    slot: str = ""


LIST_SETTING = Setting(LIST_SCHEMA, "custom-list")


def slot_setting(slot: str, key: str) -> Setting:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", slot):
        raise RuntimeError(f"Invalid Cinnamon shortcut slot: {slot!r}")
    return Setting(SLOT_SCHEMA, key, SLOT_PATH.format(slot=slot))


def normalize_accelerator(value: str) -> tuple[frozenset[str], str]:
    """Compare Linux GTK accelerators without initializing a display.

    Primary, Ctrl, Ctl and Control all mean Control on Cinnamon. Mod1 is Alt.
    Unknown modifiers remain distinct, so a release binding is not conflated
    with an ordinary key press. Keypad Delete is a different key from Delete.
    """
    aliases = {"primary": "control", "ctrl": "control", "ctl": "control", "mod1": "alt"}
    value = value.strip().lower()
    modifiers = re.findall(r"<([^>]+)>", value)
    key = re.sub(r"<[^>]+>", "", value).strip()
    if not modifiers and "+" in key:
        *modifiers, key = key.split("+")
    key = {"del": "delete", "0xffff": "delete"}.get(key, key)
    return frozenset(aliases.get(item.strip(), item.strip()) for item in modifiers), key


def _matches(value: str, target: str) -> bool:
    return normalize_accelerator(value) == normalize_accelerator(target)


class GioBackend:
    """Native, typed GSettings access; imported lazily for headless tests."""

    def __init__(self) -> None:
        from gi.repository import Gio, GLib

        self.Gio = Gio
        self.GLib = GLib
        self.source = Gio.SettingsSchemaSource.get_default()
        if self.source is None or self.source.lookup(LIST_SCHEMA, True) is None:
            raise RuntimeError("Cinnamon keyboard settings are unavailable in this session.")
        self._settings: dict[tuple[str, str], Any] = {}

    def _get(self, setting: Setting):
        identity = (setting.schema, setting.path)
        if identity not in self._settings:
            schema = self.source.lookup(setting.schema, True)
            if schema is None:
                raise RuntimeError(f"Missing settings schema: {setting.schema}")
            self._settings[identity] = self.Gio.Settings.new_full(schema, None, setting.path or None)
        return self._settings[identity]

    def read(self, setting: Setting):
        return self._get(setting).get_value(setting.key).unpack()

    def user_value(self, setting: Setting):
        value = self._get(setting).get_user_value(setting.key)
        return value.unpack() if value is not None else None

    def writable(self, setting: Setting) -> bool:
        return self._get(setting).is_writable(setting.key)

    def write(self, setting: Setting, value) -> None:
        settings = self._get(setting)
        signature = settings.get_value(setting.key).get_type_string()
        if not settings.set_value(setting.key, self.GLib.Variant(signature, value)):
            raise RuntimeError(f"Could not write {setting.label}")

    def reset(self, setting: Setting) -> None:
        if not self.writable(setting):
            raise RuntimeError(f"Settings are locked: {setting.label}")
        self._get(setting).reset(setting.key)

    def sync(self) -> None:
        self.Gio.Settings.sync()

    def bindings(self) -> list[Binding]:
        result = []
        schemas, _ = self.source.list_schemas(True)
        for schema_id in sorted(schemas):
            if not schema_id.startswith("org.cinnamon.") or "keybindings" not in schema_id:
                continue
            schema = self.source.lookup(schema_id, True)
            for key in sorted(schema.list_keys()):
                setting = Setting(schema_id, key)
                if setting == LIST_SETTING or schema.get_key(key).get_value_type().dup_string() != "as":
                    continue
                result.append(Binding(setting, key, self.read(setting)))
        for slot in self.read(LIST_SETTING):
            if slot == "__dummy__":
                continue
            setting = slot_setting(slot, "binding")
            result.append(Binding(setting, self.read(slot_setting(slot, "name")), self.read(setting),
                                  self.read(slot_setting(slot, "command")), slot))
        return result


def default_command() -> str:
    return shlex.join([str(Path(sys.executable).absolute()), "-m", "peppermint.recovery.app"])


def default_backup_path() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    return base / "peppermint" / "recovery-shortcut-backup.json"


def build_plan(backend, command: str) -> dict:
    """Read all bindings before planning any write; never modifies settings."""
    bindings = backend.bindings()
    owned = [binding for binding in bindings if binding.slot and binding.command == command]
    if len(owned) > 1:
        raise RuntimeError("Multiple custom shortcuts already launch Peppermint Recovery; resolve them first.")
    own = owned[0] if owned else None
    conflicts = [binding for binding in bindings if binding is not own and
                 any(_matches(key, RECOVERY_KEY) for key in binding.accelerators)]
    if len(conflicts) > 1:
        raise RuntimeError("Ctrl+Alt+Delete is assigned to multiple actions: " +
                           ", ".join(binding.name or binding.setting.label for binding in conflicts) +
                           ". No shortcuts were changed.")
    if conflicts:
        replacement_conflicts = [binding for binding in bindings if
                                 any(_matches(key, REPLACEMENT_KEY) for key in binding.accelerators)]
        if replacement_conflicts:
            raise RuntimeError("Ctrl+Alt+Shift+Delete is already assigned to " +
                               ", ".join(binding.name or binding.setting.label for binding in replacement_conflicts) +
                               ". No shortcuts were changed.")

    changes = []
    moved = []

    def change(setting: Setting, after) -> None:
        before = backend.read(setting)
        if before != after:
            changes.append({"setting": asdict(setting), "before": before,
                            "before_user": backend.user_value(setting), "after": after})

    if conflicts:
        conflict = conflicts[0]
        after = [REPLACEMENT_KEY if _matches(key, RECOVERY_KEY) else key for key in conflict.accelerators]
        change(conflict.setting, after)
        moved.append({"action": conflict.name, "from": "Ctrl+Alt+Delete", "to": "Ctrl+Alt+Shift+Delete"})

    slots = backend.read(LIST_SETTING)
    if own:
        slot = own.slot
        after = list(own.accelerators)
        if not any(_matches(key, RECOVERY_KEY) for key in after):
            after.append(RECOVERY_KEY)
        change(own.setting, after)
    else:
        for index in range(10000):
            slot = f"custom{index}"
            if slot not in slots and not any(backend.read(slot_setting(slot, key))
                                             for key in ("name", "command", "binding")):
                break
        else:
            raise RuntimeError("No unused Cinnamon custom shortcut slot is available.")
        change(slot_setting(slot, "name"), RECOVERY_NAME)
        change(slot_setting(slot, "command"), command)
        change(slot_setting(slot, "binding"), [RECOVERY_KEY])
        # Keep every pre-existing list item, including Cinnamon's placeholder.
        change(LIST_SETTING, [*slots, slot])

    for item in changes:
        setting = Setting(**item["setting"])
        if not backend.writable(setting):
            raise RuntimeError(f"Keyboard setting is administratively locked: {setting.label}")
    def expected(key):
        setting = slot_setting(slot, key)
        return next((item["after"] for item in changes if item["setting"] == asdict(setting)),
                    backend.read(setting))

    return {"version": 1, "status": "planned", "command": command, "slot": slot,
            "owner": {key: expected(key) for key in ("name", "command", "binding")},
            "shortcut": "Ctrl+Alt+Delete", "moved": moved, "changes": changes}


@contextmanager
def _backup_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _save_snapshot(path: Path, snapshot: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(snapshot, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _load_snapshot(path: Path) -> dict:
    snapshot = json.loads(path.read_text())
    if snapshot.get("version") != 1 or not isinstance(snapshot.get("changes"), list):
        raise RuntimeError(f"Unrecognized shortcut backup: {path}")
    return snapshot


def _restore(backend, snapshot: dict) -> dict:
    restored, skipped = [], []
    # A custom shortcut is one action spread across three keys plus list
    # membership. Preserve the whole action if the user has repurposed it.
    protected = False
    for key, expected in snapshot.get("owner", {}).items():
        setting = slot_setting(snapshot["slot"], key)
        if backend.read(setting) == expected:
            continue
        original = next((item for item in snapshot["changes"] if item["setting"] == asdict(setting)), None)
        already_restored = setting.label in snapshot.get("restored", [])
        if original and (snapshot["status"] == "installing" or already_restored) and (
                backend.read(setting) == original["before"] and
                backend.user_value(setting) == original["before_user"]):
            continue
        protected = True
    protected_path = SLOT_PATH.format(slot=snapshot["slot"])
    target_still_used = protected and any(_matches(key, RECOVERY_KEY) for key in
                                          backend.read(slot_setting(snapshot["slot"], "binding")))
    for item in reversed(snapshot["changes"]):
        setting = Setting(**item["setting"])
        current = backend.read(setting)
        if current == item["before"] and backend.user_value(setting) == item["before_user"]:
            continue
        if protected and (setting.path == protected_path or (
                setting == LIST_SETTING and snapshot["slot"] not in item["before"])):
            skipped.append(setting.label)
            continue
        if target_still_used and isinstance(item["before"], list) and any(
                _matches(key, RECOVERY_KEY) for key in item["before"]):
            skipped.append(setting.label)
            continue
        if current != item["after"] or not backend.writable(setting):
            skipped.append(setting.label)
            continue
        if item["before_user"] is None:
            backend.reset(setting)
        else:
            backend.write(setting, item["before_user"])
        restored.append(setting.label)
    backend.sync()
    return {**snapshot, "status": "partially-restored" if skipped else "restored",
            "restored": list(dict.fromkeys([*snapshot.get("restored", []), *restored])), "skipped": skipped}


def configure_shortcut(backend, command: str | None = None, backup_path: Path | None = None,
                       *, apply: bool = False) -> dict:
    command = command or default_command()
    backup_path = Path(backup_path or default_backup_path())
    if not apply:
        return {**build_plan(backend, command), "backup": str(backup_path)}
    with _backup_lock(backup_path):
        plan = build_plan(backend, command)
        previous = _load_snapshot(backup_path) if backup_path.exists() else None
        if not plan["changes"]:
            return {**plan, "status": "already-installed", "backup": str(backup_path),
                    "moved": previous.get("moved", []) if previous else []}
        if previous and previous["status"] != "restored":
            raise RuntimeError(f"An existing recovery shortcut backup needs restoration before reinstalling: {backup_path}")
        snapshot = {**plan, "status": "installing"}
        _save_snapshot(backup_path, snapshot)
        try:
            for item in plan["changes"]:
                setting = Setting(**item["setting"])
                if backend.read(setting) != item["before"] or backend.user_value(setting) != item["before_user"]:
                    raise RuntimeError(f"Shortcut changed during installation: {setting.label}")
                backend.write(setting, item["after"])
            backend.sync()
            for item in plan["changes"]:
                if backend.read(Setting(**item["setting"])) != item["after"]:
                    raise RuntimeError("Desktop did not retain the new shortcut settings.")
        except Exception:
            _save_snapshot(backup_path, _restore(backend, snapshot))
            raise
        snapshot["status"] = "installed"
        _save_snapshot(backup_path, snapshot)
        return {**snapshot, "backup": str(backup_path)}


def restore_shortcut(backend, backup_path: Path | None = None) -> dict:
    backup_path = Path(backup_path or default_backup_path())
    with _backup_lock(backup_path):
        if not backup_path.exists():
            raise RuntimeError(f"No recovery shortcut backup exists at {backup_path}")
        restored = _restore(backend, _load_snapshot(backup_path))
        _save_snapshot(backup_path, restored)
        return {**restored, "backup": str(backup_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--plan", action="store_true", help="Inspect settings without changing anything")
    action.add_argument("--install", action="store_true", help="Install Ctrl+Alt+Delete recovery shortcut")
    action.add_argument("--restore", action="store_true", help="Restore still-owned settings from the backup")
    parser.add_argument("--command", help="Absolute recovery launch command")
    parser.add_argument("--backup", type=Path, default=default_backup_path())
    arguments = parser.parse_args(argv)
    try:
        backend = GioBackend()
        result = (restore_shortcut(backend, arguments.backup) if arguments.restore else
                  configure_shortcut(backend, arguments.command, arguments.backup, apply=arguments.install))
    except (RuntimeError, OSError, ValueError, ImportError) as error:
        print(f"Recovery shortcut: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if not result.get("skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
