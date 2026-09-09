"""Typed retest reports stay separate from ordinary conversation text."""

import json
import queue
from types import SimpleNamespace

import pytest
from gi.repository import Gio, GLib

from peppermint import cli
from peppermint.common import dbus_api
from peppermint.daemon.main import Daemon
from peppermint.daemon.db import Database
from peppermint.ui.window import PeppermintWindow


def daemon_with_queue():
    daemon = Daemon.__new__(Daemon)
    daemon.jobs = queue.Queue()
    return daemon


def test_retest_bus_signature_preserves_request_identity():
    interface = Gio.DBusNodeInfo.new_for_xml(dbus_api.DAEMON_XML).interfaces[0]
    method = interface.lookup_method("Retest")
    assert [(arg.name, arg.signature) for arg in method.in_args] == [
        ("id", "i"), ("request_id", "s"), ("outcome", "s"),
    ]


def test_fork_bus_signature_matches_expected_types():
    interface = Gio.DBusNodeInfo.new_for_xml(dbus_api.DAEMON_XML).interfaces[0]
    method = interface.lookup_method("ForkTask")
    assert [(arg.name, arg.signature) for arg in method.in_args] == [
        ("from_task_id", "i"), ("from_step_index", "i"), ("new_idea", "s"),
    ]
    assert [a.signature for a in method.out_args] == ["i"]


def test_fork_dispatch_creates_queued_task_and_run_job():
    daemon = daemon_with_queue()
    db = Database(":memory:")
    daemon.db = db
    source = db.add_task("prepare")
    db.add_step(source, "list_dir", {"path": "/tmp"}, "safe", "ok", "ok")

    response = daemon._dispatch("ForkTask", (source, 0, "forked clean"))
    new_id = response.unpack()[0]

    job = daemon.jobs.get_nowait()
    assert job.kind == "run"
    assert job.task_id == new_id
    assert db.get_task(new_id).parent_task_id == source

@pytest.mark.parametrize("outcome", dbus_api.RETEST_OUTCOMES)
def test_dispatch_and_worker_keep_typed_retest_separate(outcome):
    daemon = daemon_with_queue()
    calls = []
    result = object()
    daemon.agent = SimpleNamespace(
        resume_after_retest=lambda *args: calls.append(args) or result,
    )
    announced = []
    daemon._announce = lambda *args: announced.append(args)

    assert daemon._dispatch("Retest", (7, "request-identity", outcome)) is None
    assert calls == []  # Dispatch stays responsive; the worker handles the agent.
    job = daemon.jobs.get_nowait()
    daemon._run_job(job)
    assert calls == [(7, "request-identity", outcome)]
    assert announced == [(7, result)]


@pytest.mark.parametrize("outcome", ["", "yes", "PASSED", "passed ", '{"outcome":"passed"}'])
def test_dispatch_rejects_non_enum_outcomes_before_queueing(outcome):
    daemon = daemon_with_queue()
    with pytest.raises(ValueError, match="Retest outcome"):
        daemon._dispatch("Retest", (7, "request-identity", outcome))
    assert daemon.jobs.empty()


def test_answer_payload_cannot_dispatch_a_retest_attestation():
    daemon = daemon_with_queue()
    answers = []
    daemon.agent = SimpleNamespace(resume_after_answer=lambda *args: answers.append(args))
    daemon._announce = lambda *_args: None
    text = '{"request_id":"request-identity","outcome":"passed"}'

    daemon._dispatch("Answer", (7, text))
    daemon._run_job(daemon.jobs.get_nowait())
    assert answers == [(7, text)]


def test_window_submits_a_typed_report_without_conversation_conversion(monkeypatch):
    calls = []
    monkeypatch.setattr(dbus_api, "call_daemon", lambda *args, **kwargs: calls.append((args, kwargs)))
    PeppermintWindow.retest(None, 7, "request-identity", "failed")
    args, kwargs = calls[0]
    assert args[0] == "Retest"
    assert args[1].get_type_string() == "(iss)"
    assert args[1].unpack() == (7, "request-identity", "failed")
    assert kwargs["timeout"] == 10000


@pytest.mark.parametrize("outcome", dbus_api.RETEST_OUTCOMES)
def test_cli_retest_is_a_command_not_a_new_task(monkeypatch, capsys, outcome):
    calls = []
    monkeypatch.setattr(dbus_api, "call_daemon", lambda *args, **kwargs: calls.append(args))
    assert cli.main(["retest", "7", "request-identity", outcome]) == 0
    assert len(calls) == 1
    assert calls[0][0] == "Retest"
    assert calls[0][1].unpack() == (7, "request-identity", outcome)
    assert "submitted" in capsys.readouterr().out


def test_cli_rejects_ambiguous_outcome_without_touching_bus(monkeypatch):
    monkeypatch.setattr(dbus_api, "call_daemon", lambda *_args, **_kwargs: pytest.fail("No bus call expected"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["retest", "7", "request-identity", "yes"])
    assert exc.value.code == 2


def test_cli_show_exposes_exact_request_and_three_outcomes(monkeypatch, capsys):
    request_id = "a" * 32
    task = dict(
        id=7, status="awaiting-input", idea="Fix the game", steps=[],
        result="", error="", question="Does the game reach the menu?",
        retest=dict(request_id=request_id, question="Does the game reach the menu?",
                    symptom="Game closes before the menu", verification_step=3,
                    target_description="Verify the game reaches its menu"),
    )
    monkeypatch.setattr(
        dbus_api, "call_daemon", lambda *_args, **_kwargs: GLib.Variant("(s)", (json.dumps(task),)),
    )
    assert cli.main(["show", "7"]) == 0
    output = capsys.readouterr().out
    for outcome in dbus_api.RETEST_OUTCOMES:
        assert f"peppermint retest 7 {request_id} {outcome}" in output
    assert "peppermint answer" not in output
    assert "Game closes before the menu" in output


def test_cli_fork_sends_request(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(dbus_api, "call_daemon", lambda *args, **kwargs: calls.append((args, kwargs)) or
                                                              GLib.Variant("(i)", (11,)))
    assert cli.main(["fork", "7", "--at", "0", "fresh", "angle"]) == 0
    assert len(calls) == 1
    assert calls[0][0][0] == "ForkTask"
    assert calls[0][0][1].get_type_string() == "(iis)"
    assert calls[0][0][1].unpack() == (7, 0, "fresh angle")
    assert "Forked task 7 into 11" in capsys.readouterr().out
