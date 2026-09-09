"""Portable archive export/import for Peppermint task history.

Archive format:

- ``tasks.json``: an array of task objects including related history.
- ``metadata.json``: archive metadata and compatibility checks.
- ``media/``: copied files referenced by task history.
"""

from __future__ import annotations

import json
import re
import os
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Iterable
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from peppermint import config
from peppermint.daemon.db import _archive_tasks

ARCHIVE_FORMAT = 1
MAX_MEMBERS = 4096
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_MEMBER_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
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


def _media_references(value):
    """Find exact paths, including paths inside serialized tool/message JSON."""
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return
        try:
            expanded = Path(raw).expanduser()
        except RuntimeError:
            return
        if _looks_like_path(str(expanded)):
            if expanded.is_file():
                yield raw, expanded
            return
        if raw.startswith(("{", "[")):
            try:
                nested = json.loads(raw)
            except ValueError:
                return
            yield from _media_references(nested)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _media_references(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _media_references(item)


def _iter_media_values(value: Any) -> Iterable[Path]:
    for _, path in _media_references(value):
        yield path


def _collect_media(task: dict) -> set[Path]:
    return set(_iter_media_values(task))


def _write_archive(path: Path, tasks: list[dict], metadata: dict, media: Iterable[Path]) -> None:
    payloads = {"tasks.json": json.dumps(tasks).encode('utf-8'),
                "metadata.json": json.dumps(metadata).encode('utf-8')}
    media = sorted(media, key=str)
    sizes = [source.stat().st_size for source in media]
    if (len(media) + 3 > MAX_MEMBERS or any(size > MAX_MEMBER_BYTES for size in sizes)
            or any(len(data) > MAX_JSON_BYTES for data in payloads.values())
            or sum(sizes) + sum(map(len, payloads.values())) > MAX_TOTAL_BYTES):
        raise ValueError("Export exceeds the archive size or member limit.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.partial')
    try:
        with temporary.open('xb') as handle:
            os.chmod(temporary, 0o600)
            with zipfile.ZipFile(handle, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in payloads.items():
                    archive.writestr(name, data)
                archive.writestr("media/", b'')
                for index, source in enumerate(sorted(media, key=str), start=1):
                    # Fail rather than publish an archive missing promised bytes.
                    archive.write(source, f"media/{_safe_media_name(source, index)}")
                _validate_members(archive)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def export_archive(db, task_id: int | None, *, model: str, base_dir: Path | None = None) -> Path:
    task_ids = None if task_id is None else [int(task_id)]

    if not hasattr(db, "export_tasks_with_history"):
        raise ValueError("The active database backend does not support archive export.")

    tasks = db.export_tasks_with_history(task_ids)
    if not tasks:
        raise ValueError("No tasks were found for export.")

    normalized, _ = _archive_tasks(tasks)

    references = dict(_media_references(normalized))
    media = set(references.values())
    members = {path: f"media/{_safe_media_name(path, index)}"
               for index, path in enumerate(sorted(media, key=str), start=1)}

    metadata = {
        "version": _peppermint_version(),
        "archive_format": ARCHIVE_FORMAT,
        "export_date": _now(),
        "model": model,
        "media": {original: members[path] for original, path in references.items()},
    }

    base = base_dir or (config.DATA_DIR / "archives")
    filename = f"peppermint-{_now().replace(":", "-")}-{uuid4().hex[:8]}.peppermint"
    target = Path(base) / filename

    _write_archive(target, normalized, metadata, media)
    return target


def _validate_members(archive):
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS:
        raise ImportError("The archive contains too many files.")
    seen, total = set(), 0
    for info in infos:
        name = info.filename
        parts = PurePosixPath(name).parts
        valid = name in ("tasks.json", "metadata.json", "media/") or (
            len(parts) == 2 and parts[0] == "media" and parts[1] not in (".", "..")
            and name == '/'.join(parts) and not info.is_dir())
        if not valid or "\\" in name or name in seen:
            raise ImportError(f"Invalid or duplicate archive member: {name}")
        mode = stat.S_IFMT(info.external_attr >> 16)
        if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or info.flag_bits & 1:
            raise ImportError("Archive links, special files and encrypted members are unsupported.")
        if mode == stat.S_IFDIR and name != 'media/':
            raise ImportError("Invalid media directory.")
        seen.add(name)
        limit = MAX_JSON_BYTES if name in ("tasks.json", "metadata.json") else MAX_MEMBER_BYTES
        if info.file_size > limit:
            raise ImportError("An archive member exceeds the size limit.")
        total += info.file_size
    if total > MAX_TOTAL_BYTES:
        raise ImportError("The archive exceeds the unpacked size limit.")


def _read_payload(archive):
    _validate_members(archive)
    try:
        tasks = json.loads(archive.read("tasks.json").decode("utf-8"))
        metadata = json.loads(archive.read("metadata.json").decode("utf-8"))
    except KeyError as exc:
        raise ImportError(f"Missing required file in archive: {exc.args[0]}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImportError("The archive does not contain valid JSON files.") from exc
    if not isinstance(tasks, list):
        raise ImportError("Archive tasks payload must be an array.")
    if not isinstance(metadata, dict):
        raise ImportError("Archive metadata must be an object.")
    return tasks, metadata


def _load_archive(path: Path) -> tuple[list[dict], dict]:
    try:
        with zipfile.ZipFile(path, 'r') as archive:
            return _read_payload(archive)
    except zipfile.BadZipFile as exc:
        raise ImportError("The archive file is not a valid zip file.") from exc


def _relocate_media(value, mapping):
    if isinstance(value, list):
        return [_relocate_media(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: _relocate_media(item, mapping) for key, item in value.items()}
    if isinstance(value, str):
        if value.strip() in mapping:
            return mapping[value.strip()]
        if value.strip().startswith(("{", "[")):
            try:
                nested = json.loads(value)
            except ValueError:
                return value
            relocated = _relocate_media(nested, mapping)
            if relocated != nested:
                return json.dumps(relocated, ensure_ascii=False)
    return value


def _restore_media(archive, metadata, root):
    files = {info.filename: info for info in archive.infolist()
             if info.filename.startswith('media/') and not info.is_dir()}
    manifest = metadata.get('media', {})
    if not isinstance(manifest, dict) or any(
        not isinstance(original, str) or not isinstance(member, str) or member not in files
        for original, member in manifest.items()
    ):
        raise ImportError("The archive media mapping is invalid or refers to a missing file.")
    if not files:
        return None, {}, []
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='archive-', dir=root))
    try:
        restored = {}
        for name, info in files.items():
            target = directory / PurePosixPath(name).name
            with archive.open(info) as source, target.open('xb') as output:
                os.chmod(target, 0o600)
                shutil.copyfileobj(source, output, length=64 * 1024)
            restored[name] = str(target)
        warnings = []
        if set(files) - set(manifest.values()):
            warnings.append(f"Legacy or unmapped media was restored to {directory}; its original history links could not be relocated.")
        return directory, {original: restored[member] for original, member in manifest.items()}, warnings
    except BaseException:
        shutil.rmtree(directory)
        raise


def _check_compatibility(metadata: dict) -> None:
    if not isinstance(metadata, dict):
        raise ImportError("Archive metadata is invalid.")

    archive_format = metadata.get("archive_format", 0)
    if type(archive_format) is not int or archive_format != ARCHIVE_FORMAT:
        raise ImportError("This archive format is not supported.")

    archive_version = metadata.get("version")
    if not archive_version:
        raise ImportError("The archive does not include a Peppermint version.")

    local_version = _peppermint_version()
    try:
        archive_maj, archive_min = _parse_version(archive_version)
        local_maj, local_min = _parse_version(local_version)
    except ValueError as exc:
        raise ImportError(
            f"The archive version '{archive_version}' is not a valid Peppermint version."
        ) from exc

    if archive_maj != local_maj or archive_min > local_min:
        raise ImportError(
            f"This archive was exported with Peppermint {archive_version}, "
            f"which is not compatible with {local_version}."
        )


def import_archive(db, archive_path: str | Path, *, model: str, media_dir: Path | None = None) -> dict:
    archive_path = Path(archive_path).expanduser()
    if not archive_path.is_file():
        raise ImportError(f"Archive file does not exist: {archive_path}")
    if not hasattr(db, "import_tasks_from_portable"):
        raise ImportError("The active database backend does not support atomic archive import.")

    directory = None
    try:
        # One open ZIP keeps the validated metadata and extracted bytes together.
        with zipfile.ZipFile(archive_path, 'r') as archive:
            raw_tasks, metadata = _read_payload(archive)
            _check_compatibility(metadata)
            tasks, _ = _archive_tasks(raw_tasks)
            warnings = []
            archive_model = metadata.get("model", "")
            if archive_model and archive_model != model:
                warnings.append(
                    f"Archive model '{archive_model}' differs from this instance model '{model}'. "
                    "Imported task history may reference model-specific output.")
            ids = {task['id'] for task in tasks}
            if any(task['parent_task_id'] and task['parent_task_id'] not in ids for task in tasks):
                warnings.append("Parents not included in this archive were detached from imported tasks.")
            directory, mapping, media_warnings = _restore_media(
                archive, metadata, Path(media_dir) if media_dir else config.DATA_DIR / 'imports')
            warnings.extend(media_warnings)
            tasks = _relocate_media(tasks, mapping)
        # Finish reading/closing the archive before committing database records.
        imported = db.import_tasks_from_portable(tasks)
        return {"task_ids": imported, "warnings": warnings}
    except BaseException as exc:
        if directory is not None:
            shutil.rmtree(directory)
        if isinstance(exc, zipfile.BadZipFile):
            raise ImportError("The archive file is not a valid zip file.") from exc
        raise
