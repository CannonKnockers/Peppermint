"""Approval tokens.

A click on Allow approves one exact action, not a class of actions and not
"whatever Minty does next".

Two things can go wrong between the question and the click:

1. The action changes. The model asks to delete file A, and by the time the
   click arrives the stored call says file B. A token stops this: the token is
   a fingerprint of the exact call, and the daemon checks it again before it
   runs anything.

2. The world changes. The user approves "delete notes.txt", and in the
   meantime notes.txt becomes a link to something else. A target fingerprint
   stops this: the daemon records what the target was when it asked, and
   compares before it acts.

An approval also expires. An old question was asked about an old machine
state, so the answer to it is no longer trustworthy.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from minty import config
from minty.daemon import safety

# Arguments that name a target on disk. Their fingerprints get recorded.
PATH_ARGUMENTS = ("path", "src", "dst", "target", "file", "directory")


def canonical_args(args: dict) -> str:
    """One text form for one call, whatever order the keys arrived in."""
    return json.dumps(args or {}, sort_keys=True, separators=(",", ":"), default=str)


def make_token(task_id: int, tool: str, args: dict) -> str:
    """A short fingerprint of one exact call under one policy version."""
    material = "\x1f".join([
        str(task_id),
        str(tool),
        canonical_args(args),
        str(safety.POLICY_VERSION),
    ])
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def _describe_target(raw: str) -> dict:
    """What the target looks like right now."""
    try:
        resolved = safety.resolve(raw)
    except (OSError, RuntimeError, ValueError):
        return {"given": str(raw), "state": "unreadable"}

    record = {"given": str(raw), "resolved": str(resolved)}
    try:
        info = os.lstat(resolved)
    except FileNotFoundError:
        # "absent" is a real state. It is not the same as an empty file.
        record["state"] = "absent"
        return record
    except OSError:
        record["state"] = "unreadable"
        return record

    record["state"] = "link" if os.path.islink(resolved) else (
        "directory" if os.path.isdir(resolved) else "file")
    record["size"] = info.st_size
    record["inode"] = info.st_ino
    record["mtime"] = int(info.st_mtime)
    return record


def target_fingerprint(args: dict) -> str:
    """Record every path this call touches, and its state today."""
    targets = {
        name: _describe_target(value)
        for name, value in (args or {}).items()
        if name in PATH_ARGUMENTS and isinstance(value, str) and value
    }
    return json.dumps(targets, sort_keys=True)


def expiry_time(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(seconds=config.APPROVAL_TTL_S)).isoformat(timespec="seconds")


def is_expired(expires_at: str, now: datetime | None = None) -> bool:
    if not expires_at:
        return False
    try:
        deadline = datetime.fromisoformat(expires_at)
    except ValueError:
        return True
    now = now or datetime.now(timezone.utc)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return now > deadline


def compare_targets(recorded: str, current: str) -> str:
    """Describe what changed since the question was asked. Empty means nothing."""
    try:
        before = json.loads(recorded or "{}")
        after = json.loads(current or "{}")
    except ValueError:
        return "Minty cannot read what the target looked like before."

    changes = []
    for name in sorted(set(before) | set(after)):
        old = before.get(name, {})
        new = after.get(name, {})
        if old == new:
            continue
        if old.get("resolved") != new.get("resolved"):
            changes.append(f"`{name}` now points somewhere else")
        elif old.get("state") != new.get("state"):
            changes.append(f"`{name}` changed from {old.get('state')} to {new.get('state')}")
        elif old.get("inode") != new.get("inode"):
            changes.append(f"`{name}` is a different file now")
        else:
            changes.append(f"`{name}` changed since Minty asked")
    return "; ".join(changes)


class ApprovalError(Exception):
    """The approval does not authorise this action."""


def verify(confirmation: dict, tool: str, args: dict, task_id: int) -> None:
    """Check an approval right before the action runs. Raises on any doubt."""
    expected = make_token(task_id, tool, args)
    stored = confirmation.get("token") or ""

    if not stored:
        raise ApprovalError(
            "This approval carries no token, so Minty cannot prove it was for "
            "this action. Minty did nothing."
        )
    if stored != expected:
        raise ApprovalError(
            "The action changed after you approved it. Minty did nothing. "
            "Ask again if you still want it."
        )
    if is_expired(confirmation.get("expires_at", "")):
        raise ApprovalError(
            f"The approval was more than {config.APPROVAL_TTL_S // 60} minutes old, "
            "so Minty did not use it. The machine may have changed since it asked."
        )

    changed = compare_targets(confirmation.get("fingerprint", ""), target_fingerprint(args))
    if changed:
        raise ApprovalError(
            f"The target changed after you approved it: {changed}. Minty did nothing."
        )
