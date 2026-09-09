"""Recovery actions use disposable children; real desktop processes are never signaled."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from peppermint.recovery import processes


@pytest.fixture
def child_factory():
    children = []

    def spawn(*, ignore_term=False):
        handler = "signal.SIG_IGN" if ignore_term else "lambda *_: sys.exit(0)"
        code = ("import signal,sys,time; "
                f"signal.signal(signal.SIGTERM, {handler}); "
                "print('ready', flush=True); time.sleep(60)")
        child = subprocess.Popen([sys.executable, "-I", "-c", code], stdout=subprocess.PIPE, text=True)
        children.append(child)
        assert child.stdout.readline().strip() == "ready"
        return child

    yield spawn
    for child in children:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        child.stdout.close()


def act_on(child, action, **kwargs):
    row = processes.inspect_process(child.pid)
    return processes.act(row["pid"], row["start_ticks"], row["uid"], action, **kwargs)


def test_inspection_reads_stable_owner_and_memory(child_factory):
    child = child_factory()
    row = processes.inspect_process(child.pid)
    assert row["pid"] == child.pid
    assert row["uid"] == os.geteuid()
    assert row["uids"][1] == row["uid"]
    assert row["start_ticks"] > 0
    assert row["rss_bytes"] > 0
    assert row["name"] and row["exe_name"]
    assert row["ppid"] == os.getpid()
    assert not row["kernel_thread"]


def test_term_allows_child_to_exit_gracefully(child_factory):
    child = child_factory()
    result = act_on(child, "terminate")
    assert result["status"] == "exited"
    assert result["signal"] == "SIGTERM"
    assert child.wait(timeout=2) == 0


def test_ignored_term_does_not_escalate_until_separate_kill(child_factory):
    child = child_factory(ignore_term=True)
    result = act_on(child, "terminate", timeout=0.03)
    assert result["status"] == "timeout"
    assert result["signal"] == "SIGTERM"
    assert child.poll() is None
    result = act_on(child, "kill")
    assert result["status"] == "exited"
    assert result["signal"] == "SIGKILL"
    assert child.wait(timeout=2) == -signal.SIGKILL


def test_probe_sends_no_signal(child_factory, monkeypatch):
    child = child_factory()
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda *_: pytest.fail("Probe must never send a signal"))
    result = act_on(child, "probe")
    assert result["status"] == "ok"
    assert result["euid"] == os.geteuid()
    assert child.poll() is None


@pytest.mark.parametrize("change", [{"start_ticks": -1}, {"uid": 1}])
def test_stale_selection_never_signals(child_factory, monkeypatch, change):
    child = child_factory()
    row = processes.inspect_process(child.pid)
    for key, increment in change.items():
        row[key] += increment
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda *_: pytest.fail("Stale process must not be signaled"))
    assert processes.act(row["pid"], row["start_ticks"], row["uid"], "kill")["status"] == "mismatch"
    assert child.poll() is None


def test_pid_reuse_between_selection_and_pidfd_is_rejected(child_factory, monkeypatch):
    child = child_factory()
    row = processes.inspect_process(child.pid)
    real_inspect = processes.inspect_process
    calls = []

    def inspect(pid):
        snapshot = real_inspect(pid)
        if pid == child.pid:
            calls.append(pid)
            if len(calls) == 2:
                snapshot["start_ticks"] += 1
        return snapshot

    monkeypatch.setattr(processes, "inspect_process", inspect)
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda *_: pytest.fail("Reused PID must not be signaled"))
    assert processes.act(row["pid"], row["start_ticks"], row["uid"], "kill")["status"] == "mismatch"
    assert child.poll() is None


@pytest.mark.parametrize("field,value", [("uids", [1, 2, 3, 4]), ("exe_name", "new-application")])
def test_credential_or_executable_change_after_pidfd_is_rejected(child_factory, monkeypatch, field, value):
    child = child_factory()
    row = processes.inspect_process(child.pid)
    real_inspect = processes.inspect_process
    calls = []

    def inspect(pid):
        snapshot = real_inspect(pid)
        if pid == child.pid:
            calls.append(pid)
            if len(calls) == 2:
                snapshot[field] = value
        return snapshot

    monkeypatch.setattr(processes, "inspect_process", inspect)
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda *_: pytest.fail("Changed process must not be signaled"))
    assert processes.act(row["pid"], row["start_ticks"], row["uid"], "kill")["status"] == "mismatch"


@pytest.mark.parametrize("name", ["cinnamon", "Xorg", "cinnamon-sessio", "dbus-daemon", "polkit-gnome-au", "systemd-logind"])
def test_critical_process_name_is_protected(child_factory, monkeypatch, name):
    child = child_factory()
    row = processes.inspect_process(child.pid)
    real_inspect = processes.inspect_process

    def inspect(pid):
        snapshot = real_inspect(pid)
        if pid == child.pid:
            snapshot["name"] = name
        return snapshot

    monkeypatch.setattr(processes, "inspect_process", inspect)
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda *_: pytest.fail("Critical process must not be signaled"))
    assert processes.act(row["pid"], row["start_ticks"], row["uid"], "kill")["status"] == "protected"


def test_critical_executable_protected_even_when_comm_renamed(child_factory, monkeypatch):
    child = child_factory()
    row = processes.inspect_process(child.pid)
    row.update(name="renamed", exe_name="Xorg")
    monkeypatch.setattr(processes, "inspect_process", lambda pid: dict(row, pid=pid, ppid=1))
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda *_: pytest.fail("Critical executable must not be signaled"))
    assert processes.act(row["pid"], row["start_ticks"], row["uid"], "kill")["status"] == "protected"


@pytest.mark.parametrize("pid", [1, os.getpid(), os.getppid()])
def test_self_ancestors_and_pid_one_are_protected_without_signaling(monkeypatch, pid):
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda *_: pytest.fail("Protected PID must not be signaled"))
    row = processes.inspect_process(pid)
    assert processes.act(pid, row["start_ticks"], row["uid"], "kill")["status"] == "protected"


def test_kernel_thread_is_protected(child_factory, monkeypatch):
    child = child_factory()
    row = processes.inspect_process(child.pid)
    row["kernel_thread"] = True
    monkeypatch.setattr(processes, "inspect_process", lambda pid: dict(row, pid=pid, ppid=1))
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda *_: pytest.fail("Kernel thread must not be signaled"))
    assert processes.act(row["pid"], row["start_ticks"], row["uid"], "kill")["status"] == "protected"


def test_unreadable_executable_fails_closed(child_factory, monkeypatch):
    child = child_factory()
    row = processes.inspect_process(child.pid)
    row["exe_name"] = ""
    monkeypatch.setattr(processes, "inspect_process", lambda pid: dict(row, pid=pid, ppid=1))
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda *_: pytest.fail("Unverifiable process must not be signaled"))
    assert processes.act(row["pid"], row["start_ticks"], row["uid"], "kill")["status"] == "denied"


def test_permission_denied_is_reported_without_fallback(child_factory, monkeypatch):
    child = child_factory()

    def denied(*args):
        raise PermissionError("denied")

    monkeypatch.setattr(signal, "pidfd_send_signal", denied)
    monkeypatch.setattr(os, "kill", lambda *_: pytest.fail("PID-based fallback is forbidden"))
    assert act_on(child, "kill")["status"] == "denied"
    assert child.poll() is None


def test_unavailable_pidfds_fail_closed(child_factory, monkeypatch):
    child = child_factory()
    monkeypatch.setattr(os, "pidfd_open", None)
    monkeypatch.setattr(os, "kill", lambda *_: pytest.fail("PID-based fallback is forbidden"))
    assert act_on(child, "kill")["status"] == "unsupported"
    assert child.poll() is None


@pytest.mark.parametrize("args", [(0, 1, 1000, "kill"), (-1, 1, 1000, "kill"),
                                  (True, 1, 1000, "kill"), (10, -1, 1000, "kill"),
                                  (10, 1, -1, "kill"), (10, 1, 1000, "restart"),
                                  ("10", 1, 1000, "kill")])
def test_invalid_identity_and_action_are_rejected_without_inspecting(monkeypatch, args):
    monkeypatch.setattr(processes, "inspect_process", lambda *_: pytest.fail("Invalid target must not be inspected"))
    assert processes.act(*args)["status"] == "invalid"


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -1, 6, True, "2", 10**500])
def test_invalid_timeout_rejected_without_inspecting(monkeypatch, timeout):
    monkeypatch.setattr(processes, "inspect_process", lambda *_: pytest.fail("Invalid request must not be inspected"))
    assert processes.act(10, 1, 1000, "kill", timeout=timeout)["status"] == "invalid"


def test_scan_limits_and_no_cmdline_or_environment_reads(monkeypatch):
    reads = []
    real_read = processes._read_at

    def record(directory, name, limit):
        reads.append(name)
        return real_read(directory, name, limit)

    monkeypatch.setattr(processes, "MAX_PROCESSES", 3)
    monkeypatch.setattr(processes, "_read_at", record)
    rows = processes.list_processes()
    assert 0 < len(rows) <= 3
    assert set(reads) == {"stat", "status"}
    assert all("protected" in row and "protected_reason" in row for row in rows)
    assert [row["rss_bytes"] for row in rows] == sorted([row["rss_bytes"] for row in rows], reverse=True)


def test_stat_parser_handles_parentheses_in_name():
    fields = ["S", "7"] + ["0"] * 20
    fields[19], fields[21] = "30", "5"
    parsed = processes._parse_stat("123 (a ) tricky ( name) " + " ".join(fields), 123)
    assert parsed["name"] == "a ) tricky ( name"
    assert parsed["start_ticks"] == 30
    assert parsed["rss_bytes"] == 5 * os.sysconf("SC_PAGE_SIZE")


@pytest.mark.parametrize("args,status", [(["--check"], "ok"), ([], "invalid"),
                                        (["--check", "--pid", "1"], "invalid"),
                                        (["--pid", "no"], "invalid"),
                                        (["--arbitrary-command", "id"], "invalid")])
def test_standalone_helper_uses_json_and_no_repository_imports(tmp_path, args, status):
    helper = tmp_path / "peppermint-recovery-helper"
    helper.write_bytes(Path(processes.__file__).read_bytes())
    result = subprocess.run(["/usr/bin/python3", "-I", str(helper), *args],
                            cwd=tmp_path, capture_output=True, text=True, timeout=5)
    response = json.loads(result.stdout)
    assert response["status"] == status
    assert result.stderr == ""
    assert result.returncode == (0 if status == "ok" else 1)
    if status == "ok":
        assert response["euid"] == os.geteuid()
        assert response["pidfd_available"] is True


def test_standalone_helper_stops_only_requested_child(tmp_path, child_factory):
    selected, other = child_factory(), child_factory()
    row = processes.inspect_process(selected.pid)
    helper = tmp_path / "peppermint-recovery-helper"
    helper.write_bytes(Path(processes.__file__).read_bytes())
    helper.chmod(0o700)
    result = subprocess.run([str(helper), "--pid", str(row["pid"]), "--start-ticks", str(row["start_ticks"]),
                             "--uid", str(row["uid"]), "--action", "terminate"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=5)
    assert json.loads(result.stdout)["status"] == "exited"
    assert result.returncode == 0
    assert result.stderr == ""
    assert selected.wait(timeout=2) == 0
    assert other.poll() is None


@pytest.mark.parametrize("kind", ["symlink", "fifo", "oversize"])
def test_metadata_reader_rejects_unsafe_or_unbounded_files(tmp_path, kind):
    target = tmp_path / "stat"
    if kind == "symlink":
        (tmp_path / "target").write_text("private")
        target.symlink_to(tmp_path / "target")
    elif kind == "fifo":
        os.mkfifo(target)
    else:
        target.write_text("x" * 100)
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises((OSError, ValueError)):
            processes._read_at(directory, "stat", 50)
    finally:
        os.close(directory)


def test_pidfd_is_closed_after_identity_mismatch(child_factory, monkeypatch):
    child = child_factory()
    row = processes.inspect_process(child.pid)
    real_open = os.pidfd_open
    descriptors = []

    def opened(pid, flags):
        descriptor = real_open(pid, flags)
        descriptors.append(descriptor)
        row["start_ticks"] += 1
        return descriptor

    monkeypatch.setattr(os, "pidfd_open", opened)
    monkeypatch.setattr(processes, "inspect_process", lambda pid: dict(row, pid=pid, ppid=1))
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda *_: pytest.fail("Mismatched identity must not be signaled"))
    assert processes.act(child.pid, row["start_ticks"], row["uid"], "kill")["status"] == "mismatch"
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
