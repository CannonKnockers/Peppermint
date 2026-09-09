#!/usr/bin/python3 -I
"""Bounded process inspection and exact-target recovery for Linux.

This module is also the complete, standalone privileged helper. Install a
root-owned copy at /usr/local/libexec/peppermint-recovery-helper; never launch
the writable source checkout as root. It imports only the Python standard
library, reads no command lines or environments, and cannot execute commands.

Recovery covers individual applications while the kernel and desktop still
respond. It deliberately refuses PID 1, its own ancestry, kernel threads, and
known display/session/service infrastructure. This list cannot identify every
possible critical service on every distribution. A process that ignores TERM
requires a separate explicit KILL action; the helper never escalates a signal.
"""

from __future__ import annotations

import argparse
import errno
import json
import math
import os
from pathlib import PurePath
import select
import signal
import stat
import sys
import time


MAX_PROCESSES = 4096
MAX_PROC_ENTRIES = 16384
SCAN_TIMEOUT = 0.75
MAX_ACTION_TIMEOUT = 5.0
MAX_PID = 2**31 - 1
MAX_UID = 2**32 - 2
_PROC = "/proc"
_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")
_CRITICAL_NAMES = frozenset({
    "init", "systemd", "dbus-daemon", "dbus-broker", "dbus-broker-launch",
    "cinnamon", "cinnamon-session", "cinnamon-session-binary",
    "cinnamon-screensaver", "xorg", "x", "xwayland", "wayland",
    "gnome-shell", "gnome-session", "gnome-session-binary",
    "gnome-session-check-accelerated", "gnome-settings-daemon",
    "mate-session", "mate-settings-daemon", "marco", "xfce4-session",
    "xfwm4", "xfsettingsd", "kwin_wayland", "kwin_x11", "plasmashell",
    "ksmserver", "startplasma-wayland", "startplasma-x11", "sway", "weston",
    "lightdm", "slick-greeter", "gdm", "gdm3", "gdm-session-worker",
    "gdm-x-session", "gdm-wayland-session", "sddm", "sddm-helper",
    "polkitd", "polkit-gnome-authentication-agent-1", "mate-polkit",
    "polkit-kde-authentication-agent-1", "pkexec", "login", "agetty",
    "networkmanager", "wpa_supplicant", "udevd", "elogind",
})
_CRITICAL_COMM = _CRITICAL_NAMES | frozenset(name[:15] for name in _CRITICAL_NAMES)


def _integer(value: object, *, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _read_at(directory: int, name: str, limit: int) -> str:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(name, flags, dir_fd=directory)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Unexpected process metadata file type.")
        data = os.read(descriptor, limit + 1)
        if len(data) > limit:
            raise ValueError("Process metadata exceeded the read limit.")
        return data.decode("utf-8", errors="replace")
    finally:
        os.close(descriptor)


def _parse_stat(data: str, pid: int) -> dict:
    opening, closing = data.find("("), data.rfind(")")
    if opening < 1 or closing < opening or data[:opening].strip() != str(pid):
        raise ValueError("Invalid process metadata.")
    fields = data[closing + 1:].split()
    if len(fields) < 22:
        raise ValueError("Incomplete process metadata.")
    result = {
        "pid": pid,
        "name": data[opening + 1:closing],
        "state": fields[0],
        "ppid": int(fields[1]),
        "start_ticks": int(fields[19]),
        "rss_bytes": max(0, int(fields[21])) * _PAGE_SIZE,
        "kernel_thread": bool(int(fields[6]) & 0x00200000),
    }
    if result["start_ticks"] < 0 or not _integer(result["ppid"], minimum=0, maximum=MAX_PID):
        raise ValueError("Invalid process identity.")
    return result


def inspect_process(pid: int) -> dict:
    """Read PID, start time, effective UID, name and resident memory stably.

    Missing, unreadable, or inconsistent metadata raises an exception. The
    directory and files are opened without following symlinks, and both stat
    and credentials are reread before returning an identity.
    """
    if not _integer(pid, minimum=1, maximum=MAX_PID):
        raise ValueError("PID must be a positive integer.")
    directory = os.open(f"{_PROC}/{pid}", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        first = _parse_stat(_read_at(directory, "stat", 8192), pid)
        credentials = _read_uids(directory)
        try:
            executable = os.readlink("exe", dir_fd=directory)
            executable = executable.removesuffix(" (deleted)")
            first["exe_name"] = PurePath(executable).name
        except FileNotFoundError:
            first["exe_name"] = ""
        except PermissionError:
            # Other owners' executables may be hidden. The elevated helper
            # inspects them again before authorizing a signal.
            first["exe_name"] = ""
        last = _parse_stat(_read_at(directory, "stat", 8192), pid)
        if first["start_ticks"] != last["start_ticks"] or credentials != _read_uids(directory):
            raise ProcessLookupError("Process identity changed during inspection.")
        last["exe_name"] = first["exe_name"]
        last["uid"] = credentials[1]
        last["uids"] = credentials
        return last
    finally:
        os.close(directory)


def _read_uids(directory: int) -> list[int]:
    for line in _read_at(directory, "status", 65536).splitlines():
        if line.startswith("Uid:"):
            values = [int(value) for value in line.split()[1:]]
            if len(values) == 4 and all(_integer(value, minimum=0, maximum=MAX_UID) for value in values):
                return values
            break
    raise ValueError("Process owner could not be verified.")


def _ancestors() -> set[int]:
    ancestors = {1}
    current = os.getpid()
    for _ in range(256):
        if current <= 0 or current in ancestors:
            break
        ancestors.add(current)
        try:
            current = inspect_process(current)["ppid"]
        except (OSError, ValueError):
            # The immediate parent remains protected even if procfs access
            # to the running helper itself fails.
            ancestors.add(os.getppid())
            break
    return ancestors


def _protection_reason(process: dict, ancestors: set[int]) -> str:
    if process["pid"] in ancestors:
        return "This process is part of the recovery tool or its system/session ancestry."
    if process.get("kernel_thread"):
        return "Kernel threads cannot be stopped by application recovery."
    for key, names in (("name", _CRITICAL_COMM), ("exe_name", _CRITICAL_NAMES)):
        name = process.get(key, "").lower()
        if name in names or name.startswith(("systemd-", "gsd-", "csd-")):
            return "Display, session, authentication, and core system services are protected."
    return ""


def list_processes() -> list[dict]:
    """Return a bounded snapshot, with the largest resident applications first."""
    processes: list[dict] = []
    deadline = time.monotonic() + SCAN_TIMEOUT
    ancestors = _ancestors()
    with os.scandir(_PROC) as entries:
        for index, entry in enumerate(entries):
            if index >= MAX_PROC_ENTRIES or len(processes) >= MAX_PROCESSES or time.monotonic() >= deadline:
                break
            if not entry.name.isascii() or not entry.name.isdigit():
                continue
            try:
                row = inspect_process(int(entry.name))
            except (OSError, ValueError):
                continue
            reason = _protection_reason(row, ancestors)
            row["protected"] = bool(reason)
            row["protected_reason"] = reason
            row["name"] = "".join(char if char.isprintable() else "?" for char in row["name"])[:128]
            processes.append(row)
    return sorted(processes, key=lambda row: (-row["rss_bytes"], row["pid"]))


def _result(status: str, message: str, **details: object) -> dict:
    return {"status": status, "message": message, **details}


def _pidfd_supported() -> bool:
    return callable(getattr(os, "pidfd_open", None)) and callable(getattr(signal, "pidfd_send_signal", None))


def _identity_matches(process: dict, pid: int, start_ticks: int, uid: int) -> bool:
    return process["pid"] == pid and process["start_ticks"] == start_ticks and process["uid"] == uid


def act(pid: int, start_ticks: int, uid: int, action: str, *, timeout: float = 2.0) -> dict:
    """Signal exactly one verified process, or probe without sending any signal.

    pidfds prevent PID reuse from redirecting a signal. Credentials and
    protected identities are rechecked after opening the pidfd. There is no
    fallback to a PID-based kill and no automatic TERM-to-KILL escalation.
    """
    if (not _integer(pid, minimum=1, maximum=MAX_PID)
            or not _integer(start_ticks, minimum=0, maximum=2**64 - 1)
            or not _integer(uid, minimum=0, maximum=MAX_UID)
            or action not in ("terminate", "kill", "probe")
            or type(timeout) not in (int, float) or not 0 <= timeout <= MAX_ACTION_TIMEOUT
            or not math.isfinite(timeout)):
        return _result("invalid", "Invalid process identity, action, or timeout.")
    if not _pidfd_supported():
        return _result("unsupported", "Safe process signaling is unavailable on this Python/kernel; no signal was sent.")
    descriptor = None
    try:
        before = inspect_process(pid)
        if not _identity_matches(before, pid, start_ticks, uid):
            return _result("mismatch", "The selected process changed. Refresh the process list; no signal was sent.")
        ancestors = _ancestors()
        reason = _protection_reason(before, ancestors)
        if reason:
            return _result("protected", reason)
        if before["state"] in ("Z", "X", "x"):
            return _result("exited", "The selected process has already exited.", pid=pid)
        if not before["exe_name"]:
            return _result("denied", "The executable identity could not be verified; no signal was sent.", pid=pid)
        descriptor = os.pidfd_open(pid, 0)
        after = inspect_process(pid)
        if (not _identity_matches(after, pid, start_ticks, uid)
                or before["uids"] != after["uids"]
                or before["exe_name"] != after["exe_name"]):
            return _result("mismatch", "The selected process changed. Refresh the process list; no signal was sent.")
        reason = _protection_reason(after, ancestors)
        if reason:
            return _result("protected", reason)
        if after["state"] in ("Z", "X", "x"):
            return _result("exited", "The selected process has already exited.", pid=pid)
        if action == "probe":
            return _result("ok", "The helper can inspect this exact process; no signal was sent.", euid=os.geteuid(), pid=pid)
        requested_signal = signal.SIGTERM if action == "terminate" else signal.SIGKILL
        signal.pidfd_send_signal(descriptor, requested_signal, None, 0)
        poller = select.poll()
        poller.register(descriptor, select.POLLIN)
        deadline = time.monotonic() + timeout
        while True:
            try:
                events = poller.poll(max(0, math.ceil((deadline - time.monotonic()) * 1000)))
            except InterruptedError:
                if time.monotonic() >= deadline:
                    events = []
                else:
                    continue
            if events and any(mask & select.POLLIN for _, mask in events):
                return _result("exited", "The selected process has exited.", pid=pid, signal=requested_signal.name)
            if events:
                return _result("error", "The signal was sent, but process exit could not be confirmed.", pid=pid)
            return _result("timeout", "The signal was sent, but the process has not exited yet. Refresh before choosing another action.",
                           pid=pid, signal=requested_signal.name)
    except (FileNotFoundError, ProcessLookupError):
        return _result("exited", "The selected process no longer exists.", pid=pid)
    except PermissionError:
        return _result("denied", "Permission to inspect or signal this process was denied.", pid=pid)
    except OSError as error:
        if error.errno in (errno.ENOSYS, errno.ENODEV, errno.EOPNOTSUPP):
            return _result("unsupported", "The kernel does not support safe process signaling; no fallback was used.")
        return _result("error", "Process recovery failed because operating-system access was unavailable.", errno=error.errno)
    except ValueError:
        return _result("error", "Process identity could not be verified; no further action was taken.")
    finally:
        if descriptor is not None:
            os.close(descriptor)


class _JSONParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def main(argv: list[str] | None = None) -> int:
    parser = _JSONParser(description=__doc__, add_help=False, allow_abbrev=False)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--start-ticks", type=int)
    parser.add_argument("--uid", type=int)
    parser.add_argument("--action", choices=("probe", "terminate", "kill"))
    try:
        args = parser.parse_args(argv)
        identity = (args.pid, args.start_ticks, args.uid, args.action)
        if args.check:
            if any(value is not None for value in identity):
                raise ValueError("--check cannot be combined with a process action.")
            available = _pidfd_supported()
            if available:
                descriptor = os.pidfd_open(os.getpid(), 0)
                os.close(descriptor)
            result = _result("ok" if available else "unsupported",
                             "Recovery helper is available." if available else "Safe process signaling is unavailable.",
                             euid=os.geteuid(), pidfd_available=available)
        elif any(value is None for value in identity):
            raise ValueError("Specify --check or all of --pid, --start-ticks, --uid, and --action.")
        else:
            result = act(args.pid, args.start_ticks, args.uid, args.action)
    except (ValueError, OSError) as error:
        result = _result("invalid" if isinstance(error, ValueError) else "error",
                         str(error)[:240], euid=os.geteuid())
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0 if result["status"] in ("ok", "exited") else 1


if __name__ == "__main__":
    sys.exit(main())
