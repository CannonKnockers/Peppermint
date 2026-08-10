"""File tools: read, list, search, write, move, and delete."""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
from pathlib import Path

from minty.daemon import safety
from minty.daemon.tools.registry import Confirm, Context, ToolError, tool, truncate

HOME = Path.home()
MAX_ENTRIES = 300
MAX_READ_BYTES = 200_000


def _resolve(path: str) -> Path:
    if not path:
        raise ToolError("The path is empty.")
    expanded = Path(os.path.expandvars(path)).expanduser()
    if not expanded.is_absolute():
        expanded = HOME / expanded
    return expanded


@tool(
    name="list_dir",
    description="List the files and folders in a directory. Use this before you move or sort files.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The directory. Use ~ for the home directory."},
            "show_hidden": {"type": "boolean", "description": "Include names that start with a dot."},
        },
        "required": ["path"],
    },
)
def list_dir(path: str, show_hidden: bool = False):
    target = _resolve(path)
    if not target.exists():
        raise ToolError(f"`{target}` does not exist.")
    if not target.is_dir():
        raise ToolError(f"`{target}` is a file, not a directory.")

    lines = []
    try:
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        raise ToolError(f"You do not have permission to read `{target}`.")

    for entry in entries[:MAX_ENTRIES]:
        if not show_hidden and entry.name.startswith("."):
            continue
        try:
            if entry.is_dir():
                lines.append(f"{entry.name}/")
            else:
                size = entry.stat().st_size
                lines.append(f"{entry.name}\t{size} bytes")
        except OSError:
            lines.append(f"{entry.name}\t[unreadable]")

    if len(entries) > MAX_ENTRIES:
        lines.append(f"... and {len(entries) - MAX_ENTRIES} more entries")
    if not lines:
        return f"`{target}` is empty."
    return f"{target}:\n" + "\n".join(lines)


@tool(
    name="read_file",
    description="Read the text of a file.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "max_lines": {"type": "integer", "description": "Read only the first N lines."},
        },
        "required": ["path"],
    },
)
def read_file(path: str, max_lines: int = 0):
    target = _resolve(path)
    verdict = safety.classify_path_read(str(target))
    if not verdict.safe:
        raise ToolError(f"Minty does not read this path, because it {verdict.reason}.")
    if not target.exists():
        raise ToolError(f"`{target}` does not exist.")
    if target.is_dir():
        raise ToolError(f"`{target}` is a directory. Use list_dir.")
    if target.stat().st_size > MAX_READ_BYTES:
        raise ToolError(f"`{target}` is larger than {MAX_READ_BYTES} bytes. Read part of it with run_shell and head.")

    try:
        text = target.read_text(errors="replace")
    except OSError as exc:
        raise ToolError(f"Minty could not read `{target}`: {exc}")

    if max_lines and max_lines > 0:
        text = "\n".join(text.splitlines()[:max_lines])
    return truncate(text)


@tool(
    name="search_files",
    description="Find files by name pattern below a directory. Use * as a wildcard.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "For example *.pdf"},
            "root": {"type": "string", "description": "Where to start. Default is the home directory."},
            "max_results": {"type": "integer"},
        },
        "required": ["pattern"],
    },
)
def search_files(pattern: str, root: str = "~", max_results: int = 100):
    base = _resolve(root)
    if not base.is_dir():
        raise ToolError(f"`{base}` is not a directory.")
    max_results = max(1, min(int(max_results or 100), 500))

    found = []
    skip = {".git", "node_modules", "__pycache__", ".cache", ".venv", "venv"}
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        for name in filenames:
            if fnmatch.fnmatch(name.lower(), pattern.lower()):
                found.append(str(Path(dirpath) / name))
                if len(found) >= max_results:
                    return f"{len(found)} matches (limit reached):\n" + "\n".join(found)
    if not found:
        return f"No file below `{base}` matches `{pattern}`."
    return f"{len(found)} matches:\n" + "\n".join(found)


@tool(
    name="write_file",
    description=(
        "Write text to a file. A new file in your home directory is written at once. "
        "A write that replaces an existing file needs approval."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "executable": {"type": "boolean", "description": "Make the file executable, for a script."},
        },
        "required": ["path", "content"],
    },
)
def write_file(path: str, content: str, executable: bool = False, ctx: Context = None):
    target = _resolve(path)
    verdict = safety.classify_path_write(str(target))
    if not verdict.safe and not (ctx and ctx.approved):
        return Confirm(
            description=f"Write {len(content)} characters to {target}",
            reason=verdict.reason,
        )

    if target.exists() and ctx and ctx.db:
        try:
            ctx.db.record_undo(ctx.task_id, "file", str(target), target.read_text(errors="replace"))
        except OSError:
            pass

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(content)
    except OSError as exc:
        raise ToolError(f"Minty could not write `{target}`: {exc}")
    if executable:
        target.chmod(target.stat().st_mode | 0o755)
    return f"Wrote {len(content)} characters to {target}."


@tool(
    name="make_dir",
    description="Make a folder. Minty makes the parent folders too. Use this before you move files into a new folder.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
)
def make_dir(path: str, ctx: Context = None):
    target = _resolve(path)
    if target.is_dir():
        return f"`{target}` already exists."
    verdict = safety.classify_path_write(str(target / "probe"))
    if not verdict.safe and not (ctx and ctx.approved):
        return Confirm(description=f"Make the folder {target}", reason=verdict.reason)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ToolError(f"Minty could not make `{target}`: {exc}")
    return f"Made the folder {target}."


@tool(
    name="move_file",
    description=(
        "Move or rename a file. If `dst` is a folder, or `dst` has no file ending, "
        "Minty puts the file inside that folder and makes the folder if it is missing. "
        "To rename a file, give `dst` with the new file name and its ending."
    ),
    parameters={
        "type": "object",
        "properties": {
            "src": {"type": "string", "description": "The file to move."},
            "dst": {"type": "string", "description": "The target folder, or the new file path."},
        },
        "required": ["src", "dst"],
    },
)
def move_file(src: str, dst: str, ctx: Context = None):
    source = _resolve(src)
    dest = _resolve(dst)
    if not source.exists():
        raise ToolError(f"`{source}` does not exist.")

    src_verdict = safety.classify_path_write(str(source.parent / "probe"))
    dst_verdict = safety.classify_path_write(str(dest / "probe"))
    outside = "outside your home directory"
    if outside in src_verdict.reason or outside in dst_verdict.reason:
        if not (ctx and ctx.approved):
            return Confirm(description=f"Move {source} to {dest}",
                           reason="the move leaves your home directory")

    # A target with no file ending means a folder. Without this rule, a move to
    # a missing folder renames the file to the folder name.
    treat_as_folder = dest.is_dir() or (not dest.exists() and not dest.suffix)
    if treat_as_folder:
        dest.mkdir(parents=True, exist_ok=True)
        dest = dest / source.name

    if dest.exists() and not (ctx and ctx.approved):
        return Confirm(description=f"Replace {dest} with {source}", reason="the target file exists")
    if dest == source:
        return f"`{source}` is already in the right place."

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(source), str(dest))
    except OSError as exc:
        raise ToolError(f"Minty could not move the file: {exc}")
    if ctx and ctx.db:
        ctx.db.record_undo(ctx.task_id, "move", str(dest), str(source))
    return f"Moved {source} to {dest}."


@tool(
    name="sort_folder",
    description=(
        "Sort every file in a folder into subfolders by file ending. This is the correct "
        "tool to organise or tidy a folder. Give `rules` as a map from the folder name to "
        "the file endings that belong in it, for example "
        "{\"Images\": [\"jpg\", \"png\"], \"Documents\": [\"pdf\", \"txt\"]}. "
        "Minty makes each folder, moves every matching file, and reports what it did. "
        "Use this instead of many move_file calls."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The folder to sort."},
            "rules": {
                "type": "object",
                "description": "A map from a subfolder name to a list of file endings.",
                "additionalProperties": {"type": "array", "items": {"type": "string"}},
            },
            "other_folder": {
                "type": "string",
                "description": "Where to put a file that matches no rule. Leave it out to keep the file where it is.",
            },
        },
        "required": ["path", "rules"],
    },
)
def sort_folder(path: str, rules: dict, other_folder: str = "", ctx: Context = None):
    base = _resolve(path)
    if not base.is_dir():
        raise ToolError(f"`{base}` is not a folder.")

    verdict = safety.classify_path_write(str(base / "probe"))
    if "outside your home directory" in verdict.reason and not (ctx and ctx.approved):
        return Confirm(description=f"Sort the files in {base}", reason=verdict.reason)

    if not isinstance(rules, dict) or not rules:
        raise ToolError(
            'Give `rules` as a map, for example {"Images": ["jpg", "png"]}.'
        )

    # One ending maps to one folder. Later rules do not overwrite earlier ones.
    by_ending: dict[str, str] = {}
    for folder, endings in rules.items():
        if isinstance(endings, str):
            endings = [e for e in endings.replace(",", " ").split() if e]
        for ending in endings or []:
            by_ending.setdefault(str(ending).lower().lstrip("."), str(folder))

    moved: dict[str, list[str]] = {}
    skipped: list[str] = []
    failed: list[str] = []

    for entry in sorted(base.iterdir()):
        if entry.is_dir() or entry.name.startswith("."):
            continue
        ending = entry.suffix.lower().lstrip(".")
        folder = by_ending.get(ending) or (other_folder or "")
        if not folder:
            skipped.append(entry.name)
            continue

        target = base / folder
        try:
            target.mkdir(parents=True, exist_ok=True)
            destination = target / entry.name
            if destination.exists():
                skipped.append(f"{entry.name} (a file with that name is already in {folder})")
                continue
            shutil.move(str(entry), str(destination))
        except OSError as exc:
            failed.append(f"{entry.name}: {exc}")
            continue

        moved.setdefault(folder, []).append(entry.name)
        if ctx and ctx.db:
            ctx.db.record_undo(ctx.task_id, "move", str(destination), str(entry))

    total = sum(len(names) for names in moved.values())
    lines = [f"Sorted {total} files in {base}."]
    for folder in sorted(moved):
        lines.append(f"{folder}: {len(moved[folder])} files ({', '.join(sorted(moved[folder]))})")
    if skipped:
        lines.append(f"Left in place ({len(skipped)}): {', '.join(skipped)}")
        unmatched = sorted({
            Path(name.split(" (")[0]).suffix.lower().lstrip(".")
            for name in skipped
        } - {""} - set(by_ending))
        if unmatched:
            lines.append(
                "These file endings had no rule: " + ", ".join(unmatched)
                + ". Call sort_folder again with a rule for them if they belong somewhere."
            )
    if failed:
        lines.append(f"Failed ({len(failed)}): {'; '.join(failed)}")
    if not total and not skipped:
        lines.append("The folder held no files to sort.")
    return truncate("\n".join(lines))


@tool(
    name="delete_file",
    description="Send a file or folder to the trash. This always needs approval. Minty never erases data permanently.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
)
def delete_file(path: str, ctx: Context = None):
    target = _resolve(path)
    if not target.exists():
        raise ToolError(f"`{target}` does not exist.")
    if not (ctx and ctx.approved):
        return Confirm(
            description=f"Move {target} to the trash",
            reason="a delete removes data from its current place",
        )

    trash = shutil.which("gio")
    if trash:
        proc = subprocess.run([trash, "trash", str(target)], capture_output=True, text=True, timeout=30)
        if proc.returncode == 0:
            return f"Moved {target} to the trash. You can restore it from the Trash folder."
        raise ToolError(f"The trash command failed: {proc.stderr.strip()}")
    raise ToolError("The `gio` command is missing, so Minty cannot use the trash safely.")
