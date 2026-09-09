"""Administrator transport tests never invoke pkexec or send real signals."""

import json
import stat
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from peppermint.recovery import admin


class FakeProcess:
    def __init__(self, stdout='{"status": "ok", "euid": 0}', returncode=0, polls=()):
        self.stdout_value = stdout
        self.returncode = returncode
        self.polls = iter(polls)
        self.events = []
        self.stdout = Mock()
        self.stderr = Mock()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.events.append('context-exit')
    def poll(self):
        return next(self.polls, self.returncode)
    def communicate(self, timeout=None):
        self.events.append(('communicate', timeout))
        return self.stdout_value, 'private authentication detail'
    def terminate(self):
        self.events.append('terminate')


@pytest.fixture
def transport(monkeypatch):
    process = FakeProcess()
    popen = Mock(return_value=process)
    monkeypatch.setattr(admin, 'helper_ready', lambda: True)
    monkeypatch.setattr(admin.subprocess, 'Popen', popen)
    monkeypatch.setattr(admin.time, 'sleep', lambda _: None)
    return SimpleNamespace(process=process, popen=popen)


def node(mode, uid=0):
    info = SimpleNamespace(st_mode=mode, st_uid=uid)
    return SimpleNamespace(lstat=lambda: info, stat=lambda: info, parents=[])


def trusted_path():
    path = node(stat.S_IFREG | 0o755)
    path.parents = [node(stat.S_IFDIR | 0o755), node(stat.S_IFDIR | 0o755)]
    return path


def test_helper_requires_root_owned_nonwritable_regular_executable_and_trusted_parents():
    assert admin.helper_ready(trusted_path())


@pytest.mark.parametrize('index', [0, 1, 2])
@pytest.mark.parametrize('unsafe', ['user-owned', 'group-writable', 'world-writable', 'symlink'])
def test_helper_rejects_untrusted_ownership_permissions_or_symlinks_at_every_level(index, unsafe):
    path = trusted_path()
    target = [path, *path.parents][index]
    info = target.lstat()
    if unsafe == 'user-owned':
        info.st_uid = 1000
    elif unsafe == 'group-writable':
        info.st_mode |= 0o020
    elif unsafe == 'world-writable':
        info.st_mode |= 0o002
    else:
        info.st_mode = stat.S_IFLNK | 0o755
    assert not admin.helper_ready(path)


@pytest.mark.parametrize('mode', [stat.S_IFREG | 0o644, stat.S_IFDIR | 0o755, stat.S_IFIFO | 0o755])
def test_helper_rejects_nonexecutable_or_nonregular_target(mode):
    assert not admin.helper_ready(node(mode))


def test_missing_helper_is_not_ready():
    path = trusted_path()
    path.lstat = Mock(side_effect=FileNotFoundError())
    assert not admin.helper_ready(path)


def test_unavailable_helper_never_starts_authentication(transport, monkeypatch):
    monkeypatch.setattr(admin, 'helper_ready', lambda: False)
    assert admin.run_helper(['--check'])['status'] == 'unavailable'
    transport.popen.assert_not_called()


def test_cancelled_request_never_starts_authentication(transport):
    assert admin.run_helper(['--check'], lambda: True)['status'] == 'cancelled'
    transport.popen.assert_not_called()


def test_transport_uses_fixed_helper_and_no_terminal_password_agent(transport):
    assert admin.run_helper(['--check']) == {'status': 'ok', 'euid': 0}
    transport.popen.assert_called_once_with(
        ['/usr/bin/pkexec', '--disable-internal-agent', str(admin.HELPER), '--check'],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert 'terminate' not in transport.process.events


@pytest.mark.parametrize('returncode', [126, 127])
def test_cancelled_or_denied_authentication_cannot_report_success(transport, returncode):
    transport.process.returncode = returncode
    result = admin.run_helper(['--check'])
    assert result['status'] == 'denied'
    assert 'private authentication detail' not in result['message']


@pytest.mark.parametrize('response', ['not-json', '[]', 'null', 'false', '{}', '{"euid": 0}'])
def test_invalid_helper_output_returns_controlled_error(transport, response):
    transport.process.stdout_value = response
    result = admin.run_helper(['--check'])
    assert result['status'] == 'error'
    assert 'private authentication detail' not in result['message']


def test_valid_process_failure_is_preserved_for_user(transport):
    result = {'status': 'mismatch', 'message': 'Process identity changed.'}
    transport.process.stdout_value = json.dumps(result)
    transport.process.returncode = 1
    assert admin.run_helper(['--pid', '999999']) == result


def test_launch_failure_returns_visible_error(transport):
    transport.popen.side_effect = FileNotFoundError('fixture pkexec missing')
    result = admin.run_helper(['--check'])
    assert result['status'] == 'error'
    assert 'fixture pkexec missing' in result['message']


def test_cancellation_after_launch_requests_termination_with_bounded_wait(transport):
    transport.process.polls = iter([None])
    cancellations = iter([False, True])
    result = admin.run_helper(['--check'], lambda: next(cancellations))
    assert result['status'] == 'cancelled'
    assert 'terminate' in transport.process.events
    assert ('communicate', 3) in transport.process.events


def test_authentication_deadline_cancels_without_unbounded_polling(transport, monkeypatch):
    transport.process.polls = iter([None])
    ticks = iter([0.0, 121.0])
    monkeypatch.setattr(admin.time, 'monotonic', lambda: next(ticks))
    result = admin.run_helper(['--check'])
    assert result['status'] == 'cancelled'
    assert 'terminate' in transport.process.events


@pytest.mark.parametrize('error', [ProcessLookupError(), PermissionError()])
def test_cancellation_handles_process_exit_and_root_helper_permissions(transport, error):
    transport.process.polls = iter([None])
    transport.process.terminate = Mock(side_effect=error)
    cancellations = iter([False, True])
    result = admin.run_helper(['--check'], lambda: next(cancellations))
    assert result['status'] == 'cancelled'
    assert ('communicate', 3) in transport.process.events


def test_slow_root_helper_cleanup_is_deferred_after_bounded_cancel_wait(transport, monkeypatch):
    transport.process.returncode = None
    transport.process.communicate = Mock(side_effect=subprocess.TimeoutExpired('fixture-helper', 3))
    transport.process.wait = Mock(side_effect=AssertionError('Cancellation must not wait indefinitely'))
    reaper = Mock()
    thread = Mock(return_value=reaper)
    monkeypatch.setattr(admin.threading, 'Thread', thread)
    cancellations = iter([False, True])
    result = admin.run_helper(['--check'], lambda: next(cancellations))
    assert result['status'] == 'cancelled'
    transport.process.communicate.assert_called_once_with(timeout=3)
    transport.process.wait.assert_not_called()
    assert 'context-exit' not in transport.process.events
    thread.assert_called_once_with(target=transport.process.communicate, daemon=True,
                                   name='peppermint-admin-reaper')
    reaper.start.assert_called_once_with()


def test_admin_action_serializes_only_exact_identity_and_action(monkeypatch):
    result = {'status': 'timeout', 'message': 'Still running'}
    helper = Mock(return_value=result)
    monkeypatch.setattr(admin, 'run_helper', helper)
    target = {'pid': 999999, 'start_ticks': 100, 'uid': 2000, 'name': 'ignored; command text'}
    cancelled = lambda: False
    assert admin.act_as_admin(target, 'terminate', cancelled) is result
    helper.assert_called_once_with(
        ['--pid', '999999', '--start-ticks', '100', '--uid', '2000', '--action', 'terminate'], cancelled)
