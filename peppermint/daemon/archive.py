"""Portable archive export/import for Peppermint task history.

Archive format:

- ``tasks.json``: an array of task objects including related history.
- ``metadata.json``: archive metadata and compatibility checks.
- ``media/``: copied files referenced by task history.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections.abc import Iterable
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any
from uuid import uuid4

from peppermint import config
from peppermint.daemon.db import _archive_tasks

ARCHIVE_FORMAT = 1
_VERSION_PATTERN = re.compile(r"^\s*(\d+)\.(\d+)(?:\.(\d+))?\s*$")
_MEDIA_PATH_RE = re.compile(r"^(?:/~|/)")


class ImportError(ValueError):
    """Raised when an archive payload is invalid or unsupported."""


def _peppermint_version() -> str:
    try:
        return package_version("peppermint")
    except PackageNotFoundError:
        return "0.1.0"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_version(value: str) -> tuple[int, int]:
    match = _VERSION_PATTERN.match(str(value))
    if not match:
        raise ValueError("Unrecognised version format.")
    major, minor = int(match.group(1)), int(match.group(2))
    return major, minor


def _safe_media_name(path: Path, index: int) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", path.name)[:120].strip("._") or "file"
    return f"{index:04d}-{stem}"


def _looks_like_path(value: str) -> bool:
    return bool(_MEDIA_PATH_RE.match((value or "").strip()))


def _iter_media_values(value: Any) -> Iterable[Path]:
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return
        if _looks_like_path(value):
            candidate = Path(value).expanduser()
            if candidate.is_file():
                yield candidate
            return

        if (value.startswith("{") and value.endswith("}")) or (value.startswith("[") and value.endswith("]")):
            try:
                nested = json.loads(value)
            except (TypeError, ValueError):
                return
            else:
                yield from _iter_media_values(nested)
        return

    if isinstance(value, dict):
        for child in value.values():
            yield from _iter_media_values(child)
        return

    if isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_media_values(child)


def _collect_media(task: dict) -> set[Path]:
    media: set[Path] = set()
    for value in task.values():
        media.update(_iter_media_values(value))
    return media


def _write_archive(path: Path, tasks: list[dict], metadata: dict, media: Iterable[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("tasks.json", json.dumps(tasks))
        archive.writestr("metadata.json", json.dumps(metadata))

        for index, source in enumerate(sorted(media, key=lambda p: str(p)), start=1):
            if not source.is_file():
                continue
            archive.write(source, f"media/{_safe_media_name(source, index)}")


def export_archive(db, task_id: int | None, *, model: str, base_dir: Path | None = None) -> Path:
    task_ids = None if task_id is None else [int(task_id)]

    if not hasattr(db, "export_tasks_with_history"):
        raise ValueError("The active database backend does not support archive export.")

    tasks = db.export_tasks_with_history(task_ids)
    if not tasks:
        raise ValueError("No tasks were found for export.")

    normalized, _ = _archive_tasks(tasks)

    media = set()
    for task in normalized:
        media.update(_collect_media(task))

    metadata = {
        "version": _peppermint_version(),
        "archive_format": ARCHIVE_FORMAT,
        "export_date": _now(),
        "model": model,
    }

    base = base_dir or (config.DATA_DIR / "archives")
    filename = f"peppermint-{_now().replace(":", "-")}-{uuid4().hex[:8]}.peppermint"
    target = Path(base) / filename

    _write_archive(target, normalized, metadata, media)
    return target


def _load_archive(path: Path) -> tuple[list[dict], dict]:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            try:
                raw_tasks = archive.read("tasks.json")
                raw_metadata = archive.read("metadata.json")
            except KeyError as exc:
                raise ImportError(f"Missing required file in archive: {exc.args[0]}") from exc
    except zipfile.BadZipFile as exc:
        raise ImportError("The archive file is not a valid zip file.") from exc

    try:
        tasks = json.loads(raw_tasks.decode("utf-8"))
        metadata = json.loads(raw_metadata.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImportError("The archive does not contain valid JSON files.") from exc

    if not isinstance(tasks, list):
        raise ImportError("Archive tasks payload must be an array.")
    if not isinstance(metadata, dict):
        raise ImportError("Archive metadata must be an object.")

    return tasks, metadata


def _check_compatibility(metadata: dict) -> None:
    if not isinstance(metadata, dict):
        raise ImportError("Archive metadata is invalid.")

    archive_format = int(metadata.get("archive_format", 0))
    if archive_format != ARCHIVE_FORMAT:
        raise ImportError("This archive format is not supported.")

    archive_version = metadata.get("version")
    if not archive_version:
        raise ImportError("The archive does not include a Peppermint version.")

    local_version = _peppermint_version()
    try:
        if _parse_version(archive_version) != _parse_version(local_version):
            raise ValueError
    except ValueError as exc:
        raise ImportError(
            f"This archive was exported with Peppermint {archive_version}, "
            f"which is not compatible with {local_version}."
        ) from exc


def import_archive(db, archive_path: str | Path, *, model: str) -> dict:
    archive_path = Path(archive_path)
    if not archive_path.is_file():
        raise ImportError(f"Archive file does not exist: {archive_path}")

    if not hasattr(db, "import_task_from_portable"):
        raise ImportError("The active database backend does not support archive import.")

    raw_tasks, metadata = _load_archive(archive_path)
    _check_compatibility(metadata)

    warnings: list[str] = []
    archive_model = metadata.get("model", "")
    if archive_model and archive_model != model:
        warnings.append(
            f"Archive model '{archive_model}' differs from this instance model '{model}'. "
            "Imported task history may reference model-specific output.")

    tasks = _archive_tasks(raw_tasks)[0]

    imported: list[int] = []
    for task in tasks:
        imported.append(db.import_task_from_portable(task))

    return {"task_ids": imported, "warnings": warnings}
