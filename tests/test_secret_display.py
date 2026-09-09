import json
import pytest
from peppermint.daemon.db import Database
from peppermint.common.secrets import history_secrets, mask
from peppermint.daemon.tools.shell import run_shell
from peppermint.daemon.tools.registry import Context, ToolError


def test_saved_password_is_masked_everywhere_in_task_presentation():
    db = Database(':memory:')
    tid = db.add_task('Add swap')
    db.add_message(tid, 'assistant', {'role': 'assistant', 'tool_calls': [
        {'function': {'name': 'ask_user', 'arguments': {'question': 'Please provide your password.'}}}]})
    db.add_message(tid, 'user', {'role': 'user', 'content': 'test-secret-123'})
    db.add_step(tid, 'run_shell', {'cmd': "echo 'test-secret-123' | sudo -S true"}, 'risky', 'test-secret-123')
    task = db.get_task(tid)
    displayed = task.to_json()
    assert 'test-secret-123' not in displayed
    assert '********' in displayed
    assert db.get_messages(tid)[-1]['content'] == 'test-secret-123'
    assert 'test-secret-123' in task.steps[0].args['cmd']


def test_password_discussion_does_not_hide_normal_reply():
    messages = [{'role': 'assistant', 'content': 'Your password stays local.'},
                {'role': 'user', 'content': 'Thanks'}]
    assert not history_secrets(messages)


@pytest.mark.parametrize('command', ['sudo fallocate -l 4G /swapfile',
                                   'sudo dd if=/dev/zero of=/swapfile bs=1M count=4096',
                                   'sudo mkswap /swapfile'])
def test_active_swap_writes_are_blocked_even_with_approval(monkeypatch, command):
    monkeypatch.setattr('peppermint.daemon.tools.shell.Path.read_text',
                        lambda self: 'Filename Type Size Used Priority\n/swapfile file 2048 2048 -1\n')
    def forbidden(*args, **kwargs):
        pytest.fail('Command must not execute')
    monkeypatch.setattr('peppermint.daemon.tools.shell.subprocess.Popen', forbidden)
    with pytest.raises(ToolError, match='active swap'):
        run_shell(command, ctx=Context(task_id=1, approved=True))
