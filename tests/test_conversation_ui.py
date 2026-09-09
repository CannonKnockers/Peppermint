"""Check rendered conversation history and controls with a real GTK display."""
import gi
import pytest

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk
from peppermint.ui.task_row import TaskRow


def labels(widget):
    result = [widget.get_text()] if isinstance(widget, Gtk.Label) else []
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            result.extend(labels(child))
    return result


@pytest.fixture(autouse=True)
def gtk_display():
    if not Gtk.init_check()[0]:
        pytest.skip('GTK display unavailable')


def test_followups_render_and_survive_list_refresh():
    task = dict(id=1, idea='First prompt', status='done', result='Second answer',
                messages=[{'role': 'user', 'content': 'First prompt'},
                          {'role': 'assistant', 'content': 'First answer'},
                          {'role': 'user', 'content': 'Follow-up prompt'},
                          {'role': 'assistant', 'content': 'Second answer'}])
    row = TaskRow(task, None)
    row.expanded = True
    row.update(task)
    assert 'Follow-up prompt' in labels(row)
    row.update(dict(id=1, idea='First prompt', status='done', messages=[], steps=[]))
    rendered = labels(row)
    assert 'Follow-up prompt' in rendered
    assert 'First answer' in rendered
    assert rendered.count('YOU') == 2
    assert rendered.count('PEPPERMINT') == 2
    assert rendered.count('Second answer') == 1
    row.destroy()


def test_draft_survives_detail_refresh_and_status_changes():
    task = dict(id=1, idea='Original prompt', status='done')
    row = TaskRow(task, None)
    row.expanded = True
    row.update(task)
    row._chat_entry.set_text('Keep my unfinished follow-up')
    row.update(dict(task, status='running'))
    assert row._chat_entry is None
    row.update(dict(task, status='done', result='Updated result'))
    assert row._chat_entry.get_text() == 'Keep my unfinished follow-up'
    row.destroy()


def test_submitting_followup_clears_draft_before_synchronous_refresh():
    task = dict(id=1, idea='Original prompt', status='done')

    class Client:
        def chat(self, task_id, text):
            self.sent = (task_id, text)
            row.update(dict(task, result='New response'))

    client = Client()
    row = TaskRow(task, client)
    row.expanded = True
    row.update(task)
    entry = row._chat_entry
    entry.set_text('Next question')
    row._on_chat(entry)
    assert client.sent == (1, 'Next question')
    assert row._chat_entry.get_text() == ''
    row.destroy()


def test_permission_controls_send_exact_decision_and_disable():
    class Client:
        def confirm(self, task_id, approved):
            self.decision = (task_id, approved)

    client = Client()
    row = TaskRow(dict(id=9, idea='Check hardware', status='awaiting-confirmation',
                       pending={'description': 'Inspect current memory use'}), client)

    def buttons(widget):
        if isinstance(widget, Gtk.Button):
            yield widget
        if isinstance(widget, Gtk.Container):
            for child in widget.get_children():
                yield from buttons(child)

    allow = next(button for button in buttons(row) if button.get_label() == 'Allow once')
    deny = next(button for button in buttons(row) if button.get_label() == 'Deny')
    assert not hasattr(client, 'decision')
    allow.clicked()
    assert client.decision == (9, True)
    assert not allow.is_sensitive()
    assert not deny.is_sensitive()
    row.destroy()


def test_stop_only_visible_while_active_and_requests_cancel():
    class Client:
        def cancel(self, task_id):
            self.cancelled = task_id

    client = Client()
    task = dict(id=7, idea='Diagnose performance', status='running')
    row = TaskRow(task, client)
    row.show_all()
    assert row.stop_button.get_visible()
    row.stop_button.clicked()
    assert client.cancelled == 7
    row.update(dict(task, status='cancelled'))
    row.show_all()
    assert not row.stop_button.get_visible()
    assert not row.spinner.get_visible()
    row.destroy()


def test_plan_renders_progress_and_survives_brief_refresh():
    task = dict(id=1, idea='Tune multitasking', status='running', plan=[
        {'description': 'Measure memory and GPU use', 'status': 'done'},
        {'description': 'Compare workload options', 'status': 'in_progress'},
        {'description': 'Verify the selected setup', 'status': 'pending'},
    ])
    row = TaskRow(task, None)
    row.expanded = True
    row.update(task)
    row.update(dict(id=1, idea=task['idea'], status='running'))
    rendered = labels(row)
    assert '✓  Measure memory and GPU use' in rendered
    assert '◉  Compare workload options' in rendered
    assert '○  Verify the selected setup' in rendered
    assert 'Recorded completion · evidence scope unavailable' in rendered
    row.destroy()


@pytest.mark.parametrize('scope,tool,caption', [
    ('inspection', 'steam_game_diagnostics', 'Inspection completed'),
    ('action', 'apt_install', 'Action completed'),
    ('command', 'run_shell', 'Command completed'),
])
def test_completed_plan_shows_evidence_scope_without_claiming_verification(scope, tool, caption):
    task = dict(id=1, idea='Investigate game', status='running', plan=[
        {'description': 'Investigate the reported failure', 'status': 'done',
         'evidence_kind': scope, 'evidence_tool': tool, 'evidence_step_id': 7},
        {'description': 'Retest the original symptom', 'status': 'pending'},
    ])
    row = TaskRow(task, None)
    row.expanded = True
    row.update(task)
    rendered = labels(row)
    assert f'{caption} · {tool} · step 7' in rendered
    assert '○  Retest the original symptom' in rendered
    assert not any('Verified' in label for label in rendered)
    row.destroy()


@pytest.mark.parametrize('prompt', ['Enter your password', 'Sudo Password:', 'Your passphrase?', 'Enter PIN', 'API key?'])
def test_password_question_masks_entry_and_survives_refresh(prompt):
    data = dict(id=1, idea='Login', status='awaiting-input', question=prompt)
    row = TaskRow(data, None)
    row._answer_entry.set_text('example-secret')
    assert not row._answer_entry.get_visibility()
    assert row._answer_entry.get_input_purpose() == Gtk.InputPurpose.PASSWORD
    row.update(dict(data, error='Please retry'))
    assert not row._answer_entry.get_visibility()
    assert row._answer_entry.get_text() == 'example-secret'
    row.destroy()
