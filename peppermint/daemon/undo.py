"""Putting a change back.

Recording an old value is not the same as being able to restore it. This
module does the restoring.

Every record says what kind of change it was, what it touched, and what the
value was before. A revert uses only that record. It never asks the model.

A revert is itself a change, so it can fail, and it says so plainly instead
of reporting success it did not achieve.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

TIMEOUT = 15


@dataclass
class Result:
    ok: bool
    message: str


def describe(record: dict) -> str:
    """One line a person can read before deciding."""
    kind = record.get("kind", "")
    target = record.get("target", "")
    old = record.get("old_value", "")
    if kind == "gsettings":
        return f"set {target} back to {old}"
    if kind == "move":
        return f"move {target} back to {old}"
    if kind == "file":
        return f"restore the earlier contents of {target}"
    return f"undo {kind} on {target}"


def _revert_gsettings(record: dict) -> Result:
    target = record.get("target", "").split()
    if len(target) != 2:
        return Result(False, f"Peppermint cannot read the setting name in `{record.get('target')}`.")
    schema, key = target
    old = record.get("old_value", "")
    if not old:
        return Result(False, f"Peppermint has no earlier value for {schema} {key}.")

    proc = subprocess.run(["gsettings", "set", schema, key, old],
                          capture_output=True, text=True, timeout=TIMEOUT)
    if proc.returncode != 0:
        return Result(False, f"gsettings refused the old value: {proc.stderr.strip()}")
    return Result(True, f"Set {schema} {key} back to {old}.")


def _revert_move(record: dict) -> Result:
    now_at = Path(record.get("target", ""))
    was_at = Path(record.get("old_value", ""))
    if not now_at.exists():
        return Result(False, f"`{now_at}` is not there any more, so Peppermint cannot move it back.")
    if was_at.exists():
        return Result(False, f"`{was_at}` exists again. Peppermint did not replace it.")
    try:
        was_at.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(now_at), str(was_at))
    except OSError as exc:
        return Result(False, f"Peppermint could not move the file back: {exc}")
    return Result(True, f"Moved {now_at.name} back to {was_at}.")


def _revert_file(record: dict) -> Result:
    target = Path(record.get("target", ""))
    old = record.get("old_value", "")
    try:
        # Write beside the file, then replace in one step. A crash part way
        # through leaves the original file whole.
        temporary = target.with_name(target.name + ".peppermint-undo")
        temporary.write_text(old)
        temporary.replace(target)
    except OSError as exc:
        return Result(False, f"Peppermint could not restore `{target}`: {exc}")
    return Result(True, f"Restored the earlier contents of {target}.")


REVERTERS = {
    "gsettings": _revert_gsettings,
    "move": _revert_move,
    "file": _revert_file,
}


def revert(record: dict) -> Result:
    """Put one recorded change back."""
    kind = record.get("kind", "")
    reverter = REVERTERS.get(kind)
    if reverter is None:
        return Result(False, f"Peppermint does not know how to undo a `{kind}` change.")
    return reverter(record)
