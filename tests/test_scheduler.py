"""Recurring tasks: no test installs timers or changes the real crontab."""
import json
import queue
import subprocess
from unittest.mock import Mock

import pytest

from peppermint.common.models import Status
from peppermint.daemon.db import Database
from peppermint.daemon.scheduler import Scheduler, parse_schedule


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / 'tasks.db')


@pytest.fixture
def scheduler(db, tmp_path):
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
    return Scheduler(db, unit_dir=tmp_path / 'units', run=runner,
                     which=lambda name: '/usr/bin/' + name,
                     command=['/opt/Peppermint App/bin/peppermint'], notify=Mock())


def finished(db):
    task_id = db.add_task('Check disk space')
    db.set_status(task_id, Status.DONE, result='Original result')
    return task_id


@pytest.mark.parametrize('text,calendar,cron', [
    ('every Monday', 'Mon *-*-* 00:00:00', '0 0 * * 1'),
    ('daily at 3pm', '*-*-* 15:00:00', '0 15 * * *'),
    ('EVERY sunday AT 12am', 'Sun *-*-* 00:00:00', '0 0 * * 0'),
    ('every day at 12pm', '*-*-* 12:00:00', '0 12 * * *'),
    ('every Friday at 23:59', 'Fri *-*-* 23:59:00', '59 23 * * 5'),
    ('every weekday at 8:05 am', 'Mon..Fri *-*-* 08:05:00', '5 8 * * 1-5'),
    ('every weekend', 'Sat,Sun *-*-* 00:00:00', '0 0 * * 6,0'),
    ('hourly', '*-*-* *:00:00', '0 * * * *'),
])
def test_parse(text, calendar, cron):
    parsed = parse_schedule(text)
    assert (parsed.on_calendar, parsed.cron_expr) == (calendar, cron)


@pytest.mark.parametrize('text', ['', 'tomorrow', 'daily at 24:00', 'daily at 4:60',
                                  'daily at 0pm', 'daily at 13am', 'daily at noon',
                                  'daily at 3pm at 4pm', 'daily\nExecStart=bad', None])
def test_reject_unsupported(text):
    with pytest.raises(ValueError):
        parse_schedule(text)


def test_systemd_lifecycle_and_persistence(scheduler, db):
    task_id = finished(db)
    schedule = scheduler.create(task_id, 'daily at 3pm')
    assert schedule['backend'] == 'systemd'
    assert schedule['enabled'] == 1
    timer = scheduler.unit_dir / f'peppermint-task-{task_id}.timer'
    service = timer.with_suffix('.service')
    assert 'OnCalendar=*-*-* 15:00:00' in timer.read_text()
    assert 'WantedBy=timers.target' in timer.read_text()
    assert '"/opt/Peppermint App/bin/peppermint" "run-scheduled" "1"' in service.read_text()
    assert Database(db.path).get_schedule(task_id)['enabled'] == 1
    assert db.get_task(task_id).to_dict()['schedule']['enabled'] is True
    assert db.list_tasks()[0].schedule['schedule_text'] == 'daily at 3pm'
    assert db.task_overview()['tasks'][0]['schedule']['enabled'] is True
    scheduler.pause(task_id)
    assert scheduler.run_scheduled(task_id) == 0
    assert not db.get_task(task_id).schedule['enabled']
    scheduler.resume(task_id)
    assert db.get_schedule(task_id)['enabled'] == 1
    scheduler.remove(task_id)
    assert scheduler.list() == []
    assert not timer.exists() and not service.exists()
    assert db.get_task(task_id) is not None
    commands = [call.args[0] for call in scheduler.run.call_args_list]
    assert ['systemctl', '--user', 'enable', '--now', 'peppermint-task-1.timer'] in commands
    assert ['systemctl', '--user', 'disable', '--now', 'peppermint-task-1.timer'] in commands
    assert all(call.kwargs['timeout'] == 20 for call in scheduler.run.call_args_list)


def test_fresh_history_and_overlap_protection(scheduler, db):
    task_id = finished(db)
    db.add_message(task_id, 'user', {'role': 'user', 'content': 'Old conversation'})
    scheduler.create(task_id, 'every Monday')
    new_id = scheduler.run_scheduled(task_id)
    task = db.get_task(new_id)
    assert new_id != task_id
    assert task.idea == db.get_task(task_id).idea
    assert task.status == 'queued'
    assert not task.steps and not task.messages and task.pending is None
    assert task.result == '' and task.parent_task_id == 0
    for status in (Status.QUEUED, Status.RUNNING, Status.AWAITING_CONFIRMATION, Status.AWAITING_INPUT):
        db.set_status(new_id, status)
        assert scheduler.run_scheduled(task_id) == 0
    db.set_status(new_id, Status.DONE)
    assert scheduler.run_scheduled(task_id) > new_id
    assert db.get_task(task_id).result == 'Original result'
    assert db.get_messages(task_id)[0]['content'] == 'Old conversation'


def test_unfinished_template_is_skipped(scheduler, db):
    task_id = db.add_task('Still being planned')
    scheduler.create(task_id, 'hourly')
    assert scheduler.run_scheduled(task_id) == 0
    db.set_status(task_id, Status.DONE)
    assert scheduler.run_scheduled(task_id) > task_id


def test_atomic_claim_across_connections(scheduler, db):
    from concurrent.futures import ThreadPoolExecutor
    task_id = finished(db)
    scheduler.create(task_id, 'hourly')
    def claim(_):
        return Database(db.path).create_scheduled_run(task_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, range(2)))
    assert sum(bool(result) for result in results) == 1


def test_install_failure_stays_paused_and_can_resume(scheduler, db):
    task_id = finished(db)
    def fail_enable(command, **kwargs):
        if 'enable' in command:
            return subprocess.CompletedProcess(command, 1, '', 'permission denied')
        return subprocess.CompletedProcess(command, 0, '', '')
    scheduler.run.side_effect = fail_enable
    with pytest.raises(RuntimeError, match='saved paused'):
        scheduler.create(task_id, 'daily')
    assert db.get_schedule(task_id)['enabled'] == 0
    assert scheduler.run_scheduled(task_id) == 0
    # No cron fallback after a partial systemd installation.
    assert not any(call.args[0][0] == 'crontab' for call in scheduler.run.call_args_list)
    scheduler.run.side_effect = None
    scheduler.resume(task_id)
    assert db.get_schedule(task_id)['enabled'] == 1


def test_pause_failure_still_gates_runs(scheduler, db):
    task_id = finished(db)
    scheduler.create(task_id, 'daily')
    scheduler.run.return_value = subprocess.CompletedProcess([], 1, '', 'manager unavailable')
    with pytest.raises(RuntimeError):
        scheduler.pause(task_id)
    assert scheduler.run_scheduled(task_id) == 0


def test_cron_fallback_preserves_other_jobs(scheduler, db):
    task_id = finished(db)
    original = 'MAILTO=owner\n0 1 * * * /bin/true # other\n0 2 * * * /bin/true # peppermint-task-10\n'
    content = original
    def runner(command, **kwargs):
        nonlocal content
        if command[0] == 'systemctl':
            return subprocess.CompletedProcess(command, 1, '', 'No user manager')
        if command == ['crontab', '-l']:
            return subprocess.CompletedProcess(command, 0, content, '')
        assert command == ['crontab', '-']
        content = kwargs['input']
        return subprocess.CompletedProcess(command, 0, '', '')
    scheduler.run.side_effect = runner
    schedule = scheduler.create(task_id, 'daily at 3pm')
    assert schedule['backend'] == 'cron' and 'warning' in schedule
    scheduler.notify.assert_called_once()
    assert content.startswith(original)
    assert '0 15 * * * XDG_RUNTIME_DIR=' in content
    assert 'DBUS_SESSION_BUS_ADDRESS=' in content
    assert "'/opt/Peppermint App/bin/peppermint' run-scheduled 1 # peppermint-task-1" in content
    scheduler.resume(task_id)
    assert content.count(' # peppermint-task-1\n') == 1
    scheduler.pause(task_id)
    assert content == original
    scheduler.resume(task_id)
    scheduler.remove(task_id)
    assert content == original


def test_cron_read_failure_never_overwrites(scheduler, db):
    scheduler.which = lambda name: '/usr/bin/crontab' if name == 'crontab' else None
    scheduler.run.return_value = subprocess.CompletedProcess([], 1, '', 'access denied')
    with pytest.raises(RuntimeError, match='access denied'):
        scheduler.create(finished(db), 'daily')
    assert all(call.args[0] != ['crontab', '-'] for call in scheduler.run.call_args_list)


def test_no_backend_and_invalid_requests_have_no_side_effects(scheduler, db):
    scheduler.which = lambda _: None
    with pytest.raises(RuntimeError, match='Neither'):
        scheduler.create(finished(db), 'daily')
    assert scheduler.list() == []
    for task_id in (0, -1, True, 2**31, '../bad'):
        with pytest.raises(ValueError):
            scheduler.create(task_id, 'daily')
    with pytest.raises(ValueError, match='does not exist'):
        scheduler.create(99, 'daily')
    scheduler.run.assert_not_called()


def test_duplicate_schedule_does_not_replace(scheduler, db):
    task_id = finished(db)
    scheduler.create(task_id, 'daily')
    scheduler.run.reset_mock()
    with pytest.raises(ValueError, match='already has'):
        scheduler.create(task_id, 'hourly')
    scheduler.run.assert_not_called()
    assert scheduler.list()[0]['schedule_text'] == 'daily'


def test_dbus_dispatch(scheduler, db):
    from peppermint.common import dbus_api
    from peppermint.daemon.main import Daemon
    from gi.repository import Gio
    daemon = Daemon.__new__(Daemon)
    daemon.db, daemon.scheduler, daemon.jobs = db, scheduler, queue.Queue()
    daemon._emit_update = Mock()
    task_id = finished(db)
    daemon._dispatch('CreateSchedule', (task_id, 'daily'))
    result = daemon._dispatch('ListSchedules', ())
    assert json.loads(result.unpack()[0])[0]['enabled'] == 1
    for method in ('PauseSchedule', 'ResumeSchedule'):
        daemon._dispatch(method, (task_id,))
    new_id = daemon._dispatch('RunScheduled', (task_id,)).unpack()[0]
    job = daemon.jobs.get_nowait()
    assert job.kind == 'run' and job.task_id == new_id
    daemon._dispatch('RunScheduled', (task_id,))
    assert daemon.jobs.empty()
    daemon._dispatch('RemoveSchedule', (task_id,))
    node = Gio.DBusNodeInfo.new_for_xml(dbus_api.DAEMON_XML)
    for method, inputs, outputs in [('CreateSchedule', 'is', 's'), ('ListSchedules', '', 's'),
                                    ('RunScheduled', 'i', 'i'), ('PauseSchedule', 'i', ''),
                                    ('ResumeSchedule', 'i', ''), ('RemoveSchedule', 'i', '')]:
        info = node.interfaces[0].lookup_method(method)
        assert ''.join(arg.signature for arg in info.in_args) == inputs
        assert ''.join(arg.signature for arg in info.out_args) == outputs


@pytest.mark.parametrize('args,method,params,response', [
    (['schedule', 'list'], 'ListSchedules', (), ('[]',)),
    (['schedule', 'add', '3', 'daily at 3pm'], 'CreateSchedule', (3, 'daily at 3pm'),
     (json.dumps({'schedule_text': 'daily at 3pm', 'backend': 'cron', 'warning': 'Using cron'}),)),
    (['schedule', 'pause', '3'], 'PauseSchedule', (3,), None),
    (['schedule', 'resume', '3'], 'ResumeSchedule', (3,), None),
    (['schedule', 'remove', '3'], 'RemoveSchedule', (3,), None),
    (['run-scheduled', '3'], 'RunScheduled', (3,), (4,)),
])
def test_cli(monkeypatch, capsys, args, method, params, response):
    from peppermint import cli
    def call(name, arguments=None, reply_type=None, **kwargs):
        assert name == method
        assert (arguments.unpack() if arguments else ()) == params
        return Mock(unpack=lambda: response)
    monkeypatch.setattr(cli.dbus_api, 'call_daemon', call)
    assert cli.main(args) == 0
    if method == 'CreateSchedule':
        assert 'Warning: Using cron' in capsys.readouterr().err


def test_clock_visible_in_task_row_and_board(scheduler, db):
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
    from peppermint.ui.task_row import TaskRow
    from peppermint.ui.task_board import TaskBoard
    if not Gtk.init_check()[0]:
        pytest.skip('GTK display unavailable')
    task_id = finished(db)
    scheduler.create(task_id, 'daily')
    task = db.get_task(task_id).to_dict()
    row = TaskRow(task, Mock())
    row.show_all()
    assert row.clock.get_visible()
    assert 'enabled' in row.clock.get_tooltip_text()
    scheduler.pause(task_id)
    row.update(db.get_task(task_id).to_dict())
    assert 'paused' in row.clock.get_tooltip_text()
    board = TaskBoard(Mock(), Mock(), Mock(), Mock())
    card = board._card(task)
    heading = card.get_children()[0]
    assert any(isinstance(w, Gtk.Image) and 'daily' in (w.get_tooltip_text() or '')
               for w in heading.get_children())
    scheduler.remove(task_id)
    row.update(db.get_task(task_id).to_dict())
    row.show_all()
    assert not row.clock.get_visible()
    row.destroy()
    card.destroy()
    board.destroy()


def test_upgrade_from_v4_preserves_tasks(tmp_path):
    path = tmp_path / 'old.db'
    db = Database(path)
    task_id = finished(db)
    with db.connection() as conn:
        conn.execute('DROP TABLE task_schedules')
        conn.execute('UPDATE schema_version SET version = 4')
    upgraded = Database(path)
    assert upgraded.current_version() == 5
    assert upgraded.list_schedules() == []
    assert upgraded.get_task(task_id).result == 'Original result'
    upgraded.add_schedule(task_id, 'daily', 'systemd', '0 0 * * *', '*-*-* 00:00:00')
    assert upgraded.get_schedule(task_id)['last_run_task_id'] is None


def test_cron_empty_table(scheduler, db):
    scheduler.which = lambda name: '/usr/bin/crontab' if name == 'crontab' else None
    def run(command, **kwargs):
        if command == ['crontab', '-l']:
            return subprocess.CompletedProcess(command, 1, '', 'no crontab for test-user')
        assert 'run-scheduled 1 # peppermint-task-1' in kwargs['input']
        return subprocess.CompletedProcess(command, 0, '', '')
    scheduler.run.side_effect = run
    assert scheduler.create(finished(db), 'daily')['enabled'] == 1


def test_resume_timeout_stays_paused(scheduler, db):
    task_id = finished(db)
    scheduler.create(task_id, 'daily')
    scheduler.pause(task_id)
    scheduler.run.side_effect = subprocess.TimeoutExpired('systemctl', 20)
    with pytest.raises(subprocess.TimeoutExpired):
        scheduler.resume(task_id)
    assert scheduler.run_scheduled(task_id) == 0


def test_removed_schedule_callback_is_noop(scheduler, db):
    task_id = finished(db)
    scheduler.create(task_id, 'daily')
    scheduler.remove(task_id)
    assert scheduler.run_scheduled(task_id) == 0
    assert len(db.list_tasks()) == 1


def test_schedule_bus_worker_is_bounded(scheduler, db, monkeypatch):
    from peppermint.daemon import main
    import threading
    daemon = main.Daemon.__new__(main.Daemon)
    daemon.db, daemon.scheduler = db, scheduler
    daemon._schedule_lock = threading.Lock()
    daemon._emit_update = Mock()
    invocations = [Mock(), Mock()]
    threads, callbacks = [], []
    monkeypatch.setattr(main.threading, 'Thread', lambda **kwargs: Mock(start=lambda: threads.append(kwargs['target'])))
    monkeypatch.setattr(main.GLib, 'idle_add', lambda callback: callbacks.append(callback))
    daemon._start_schedule('CreateSchedule', (finished(db), 'daily'), invocations[0])
    scheduler.run.assert_not_called()  # Bus returned before OS work started.
    daemon._start_schedule('PauseSchedule', (1,), invocations[1])
    invocations[1].return_dbus_error.assert_called_once()
    assert len(threads) == 1
    threads[0]()
    assert len(callbacks) == 1
    callbacks[0]()
    invocations[0].return_value.assert_called_once()
    assert not daemon._schedule_lock.locked()


@pytest.mark.parametrize('args', [['run-scheduled', '0'], ['schedule', 'pause', '-1'],
                                  ['schedule', 'resume', '2147483648'], ['schedule', 'remove']])
def test_cli_rejects_invalid_ids(args, monkeypatch):
    from peppermint import cli
    call = Mock()
    monkeypatch.setattr(cli.dbus_api, 'call_daemon', call)
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert exc.value.code == 2
    call.assert_not_called()
