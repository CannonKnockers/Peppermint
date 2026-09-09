"""Recurring task timers. OS timers only enqueue work; agent approvals still apply.

Supported phrases use local time: daily/every day, every weekday/weekend,
every Monday (any weekday), optionally 'at 3pm' or 'at 15:30', and
hourly/every hour. A missing time means midnight. Missed runs are not replayed.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import threading

from peppermint.daemon.db import Database


@dataclass(frozen=True)
class Schedule:
    text: str
    on_calendar: str
    cron_expr: str


def parse_schedule(text: str) -> Schedule:
    if not isinstance(text, str) or len(text) > 120 or '\n' in text or '\r' in text:
        raise ValueError('Use a schedule such as every Monday or daily at 3pm.')
    text = ' '.join(text.lower().split())
    if text in ('hourly', 'every hour'):
        return Schedule(text, '*-*-* *:00:00', '0 * * * *')
    parts = text.split(' at ')
    if len(parts) > 2:
        raise ValueError('Use one time, such as daily at 3pm.')
    period = parts[0]
    hour = minute = 0
    if len(parts) == 2:
        match = re.fullmatch(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', parts[1])
        if not match:
            raise ValueError('Use a time such as 3pm or 15:30.')
        hour, minute = int(match[1]), int(match[2] or 0)
        if minute > 59 or (match[3] and not 1 <= hour <= 12) or (not match[3] and hour > 23):
            raise ValueError('Time is outside the valid clock range.')
        if match[3]:
            hour = hour % 12 + (12 if match[3] == 'pm' else 0)
    weekdays = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
    calendar_day, cron_day = '', '*'
    if period in ('daily', 'every day'):
        pass
    elif period in ('weekdays', 'every weekday'):
        calendar_day, cron_day = 'Mon..Fri ', '1-5'
    elif period in ('weekends', 'every weekend'):
        calendar_day, cron_day = 'Sat,Sun ', '6,0'
    elif period.startswith('every ') and period[6:] in weekdays:
        day = period[6:]
        calendar_day, cron_day = day[:3].title() + ' ', str((weekdays.index(day) + 1) % 7)
    else:
        raise ValueError('Unsupported schedule. Try every Monday, daily at 3pm, or hourly.')
    return Schedule(text, f'{calendar_day}*-*-* {hour:02}:{minute:02}:00',
                    f'{minute} {hour} * * {cron_day}')


class Scheduler:
    def __init__(self, db: Database, *, unit_dir: Path | None = None, run=None, which=None,
                 command: list[str] | None = None, notify=None):
        self.db = db
        self.unit_dir = unit_dir or Path.home() / '.config/systemd/user'
        self.run = run or subprocess.run
        self.which = which or shutil.which
        executable = self.which('peppermint')
        self.command = command or ([executable] if executable else [sys.executable, '-m', 'peppermint.cli'])
        self.notify = notify or (lambda message: None)
        self.lock = threading.RLock()

    def _call(self, command, *, input=None, check=True):
        result = self.run(command, input=input, text=True, capture_output=True, timeout=20)
        if check and result.returncode:
            raise RuntimeError(result.stderr.strip() or f'{command[0]} failed ({result.returncode}).')
        return result

    def _systemctl(self, *args, check=True):
        return self._call(['systemctl', '--user', *args], check=check)

    def _backend(self):
        if self.which('systemctl'):
            try:
                if self._systemctl('show-environment', check=False).returncode == 0:
                    return 'systemd'
            except (OSError, subprocess.TimeoutExpired):
                pass
        if self.which('crontab'):
            return 'cron'
        raise RuntimeError('Neither a systemd user manager nor crontab is available.')

    @staticmethod
    def _name(task_id):
        if type(task_id) is not int or not 1 <= task_id <= 2_147_483_647:
            raise ValueError('Task ID must be between 1 and 2147483647.')
        return f'peppermint-task-{task_id}'

    def _get(self, task_id):
        self._name(task_id)
        schedule = self.db.get_schedule(task_id)
        if schedule is None:
            raise ValueError(f'Task {task_id} has no schedule.')
        return schedule

    def create(self, task_id, text):
        self._name(task_id)
        parsed = parse_schedule(text)
        with self.lock:
            if self.db.get_task(task_id, with_steps=False) is None:
                raise ValueError(f'Task {task_id} does not exist.')
            if self.db.get_schedule(task_id):
                raise ValueError('This task already has a schedule. Remove it before replacing it.')
            backend = self._backend()
            # Save paused first: an OS failure or daemon crash must not allow work.
            self.db.add_schedule(task_id, parsed.text, backend, parsed.cron_expr, parsed.on_calendar)
            try:
                self.resume(task_id)
            except Exception as exc:
                raise RuntimeError(f'Schedule saved paused; fix the error, then resume or remove it: {exc}') from exc
            result = self._get(task_id)
            if backend == 'cron':
                result['warning'] = 'Systemd user timers are unavailable. Using cron; the cron service and your desktop session must be running.'
                self.notify(result['warning'])
            return result

    def list(self):
        return self.db.list_schedules()

    @staticmethod
    def _unit_arg(value):
        # ExecStart has systemd escaping, not shell parsing.
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'

    def _write_units(self, schedule):
        task_id = schedule['task_id']
        name = self._name(task_id)
        command = [*self.command, 'run-scheduled', str(task_id)]
        if any('\n' in arg or '\r' in arg for arg in command):
            raise ValueError('Executable path must not contain line breaks.')
        service = ('[Unit]\nDescription=Peppermint recurring task ' + str(task_id) + '\n\n'
                   '[Service]\nType=oneshot\nExecStart=' + ' '.join(map(self._unit_arg, command)) + '\n')
        timer = (f'[Unit]\nDescription=Peppermint recurring task {task_id}\n\n[Timer]\n'
                 f'OnCalendar={schedule["on_calendar"]}\nPersistent=false\nUnit={name}.service\n\n'
                 '[Install]\nWantedBy=timers.target\n')
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        for suffix, content in (('service', service), ('timer', timer)):
            path = self.unit_dir / f'{name}.{suffix}'
            temporary = path.with_suffix(path.suffix + '.tmp')
            temporary.write_text(content, encoding='utf-8')
            temporary.replace(path)

    def _cron(self, schedule, enabled):
        result = self._call(['crontab', '-l'], check=False)
        if result.returncode and 'no crontab' not in result.stderr.lower():
            raise RuntimeError(result.stderr.strip() or 'Cannot read crontab.')
        marker = '# ' + self._name(schedule['task_id'])
        # Own only jobs with our exact suffix; preserve unrelated lines verbatim.
        output = [line for line in result.stdout.splitlines()
                  if not line.endswith(' ' + marker)]
        if enabled:
            uid = os.getuid()
            command = [*self.command, 'run-scheduled', str(schedule['task_id'])]
            prefix = f'XDG_RUNTIME_DIR=/run/user/{uid} DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus '
            job = (prefix + shlex.join(command)).replace('%', r'\%')
            if '\n' in job or '\r' in job:
                raise ValueError('Executable path must not contain line breaks.')
            output.append(schedule['cron_expr'] + ' ' + job + ' ' + marker)
        self._call(['crontab', '-'], input='\n'.join(output) + '\n')

    def pause(self, task_id):
        with self.lock:
            schedule = self._get(task_id)
            # Gate callbacks before touching the OS. A failed stop stays paused.
            self.db.set_schedule_enabled(task_id, False)
            if schedule['backend'] == 'systemd':
                self._systemctl('disable', '--now', self._name(task_id) + '.timer')
            else:
                self._cron(schedule, False)

    def resume(self, task_id):
        with self.lock:
            schedule = self._get(task_id)
            self.db.set_schedule_enabled(task_id, False)
            if schedule['backend'] == 'systemd':
                self._write_units(schedule)
                self._systemctl('daemon-reload')
                self._systemctl('enable', '--now', self._name(task_id) + '.timer')
            else:
                self._cron(schedule, True)
            self.db.set_schedule_enabled(task_id, True)

    def remove(self, task_id):
        with self.lock:
            schedule = self._get(task_id)
            self.pause(task_id)
            if schedule['backend'] == 'systemd':
                for suffix in ('timer', 'service'):
                    (self.unit_dir / f'{self._name(task_id)}.{suffix}').unlink(missing_ok=True)
                self._systemctl('daemon-reload')
            self.db.remove_schedule(task_id)

    def run_scheduled(self, task_id):
        with self.lock:
            self._name(task_id)
            return self.db.create_scheduled_run(task_id)
