"""Use a root-owned, isolated helper through the desktop's authentication agent."""

import json
import os
from pathlib import Path
import stat
import subprocess
import threading
import time

HELPER = Path('/usr/local/libexec/peppermint-recovery-helper')


def helper_ready(path=HELPER):
    try:
        for item in (path, *path.parents):
            mode = item.lstat()
            if mode.st_uid != 0 or mode.st_mode & 0o022 or stat.S_ISLNK(mode.st_mode):
                return False
        mode = path.stat().st_mode
        return stat.S_ISREG(mode) and bool(mode & stat.S_IXUSR)
    except OSError:
        return False


def run_helper(arguments, cancelled=None):
    if not helper_ready():
        return {'status': 'unavailable', 'message': 'Administrator helper is not installed. Run scripts/install-recovery-admin.sh.'}
    cancelled = cancelled or (lambda: False)
    if cancelled():
        return {'status': 'cancelled', 'message': 'Cancelled before authentication.'}
    try:
        process = subprocess.Popen(['/usr/bin/pkexec', '--disable-internal-agent', str(HELPER), *arguments],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True)
        try:
            deadline = time.monotonic() + 120
            while process.poll() is None:
                if cancelled() or time.monotonic() > deadline:
                    try:
                        process.terminate()
                    except (ProcessLookupError, PermissionError):
                        pass
                    try:
                        process.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        # An authenticated root helper cannot be killed by the
                        # UI; its own action/observation budget is at most 2s.
                        threading.Thread(target=process.communicate, daemon=True,
                                         name='peppermint-admin-reaper').start()
                    return {'status': 'cancelled', 'message': 'Authentication cancelled or timed out. An already authorized stop may have completed.'}
                time.sleep(0.05)
            stdout, _stderr = process.communicate()
            if process.returncode in (126, 127):
                return {'status': 'denied', 'message': 'Administrator authentication was cancelled or denied. No recovery action was authorized.'}
            try:
                result = json.loads(stdout)
                if not isinstance(result, dict) or 'status' not in result:
                    raise ValueError('Invalid helper response')
                return result
            except (ValueError, TypeError):
                return {'status': 'error', 'message': 'The administrator helper did not return a valid result.'}
        finally:
            if process.poll() is not None:
                process.stdout.close()
                process.stderr.close()
    except OSError as exc:
        return {'status': 'error', 'message': f'Could not start administrator authentication: {exc}'}


def act_as_admin(target, action, cancelled=None):
    return run_helper(['--pid', str(target['pid']), '--start-ticks', str(target['start_ticks']),
                       '--uid', str(target['uid']), '--action', action], cancelled)
