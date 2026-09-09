"""Example plugin that backs up the home folder."""

from __future__ import annotations

from datetime import datetime
import shutil
from pathlib import Path

import peppermint


@peppermint.tool(
    name="backup_home",
    description="Back up the current user's home folder to ~/Backups/.",
    parameters={"type": "object", "properties": {}},
    requires_approval=True,
)
def backup_home(ctx=None):
    if ctx and (approval := ctx.ask_approval("Back up this user's home folder.")):
        return approval

    home = Path.home()
    backup_root = home / "Backups"
    backup_root.mkdir(parents=True, exist_ok=True)

    target = backup_root / f"home-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(home, target, ignore=shutil.ignore_patterns("Backups"), dirs_exist_ok=False)

    if ctx:
        ctx.notify("Peppermint backup", f"Home backup created at {target}")
    return f"Backup created at {target}"
