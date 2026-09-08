"""Shell failures must remain failures, with useful diagnostic evidence."""

import shlex
import sys
import time

import pytest

from peppermint.daemon import tools
from peppermint.daemon.tools.registry import Confirm, Context, ToolError


def shell(cmd, **kwargs):
    return tools.call(
        "run_shell", {"cmd": cmd, "purpose": "Exercise shell result handling", **kwargs},
        Context(task_id=0, approved=True, require_approval=True),
    )


def test_nonzero_exit_preserves_both_output_streams():
    with pytest.raises(ToolError, match="exit code 7") as error:
        shell("printf 'partial output'; printf 'diagnostic detail' >&2; exit 7")

    assert "partial output" in str(error.value)
    assert "[stderr] diagnostic detail" in str(error.value)
    assert "Partial changes may remain" in str(error.value)


def test_nonzero_exit_without_output_is_still_an_error():
    with pytest.raises(ToolError, match="exit code 3"):
        shell("exit 3")


@pytest.mark.parametrize(("cmd", "expected"), [
    ("true", "[the command produced no output]"),
    ("printf 'complete'", "complete"),
    ("printf 'warning' >&2", "[stderr] warning"),
])
def test_zero_exit_is_successful(cmd, expected):
    assert shell(cmd) == expected


def test_timeout_stops_the_shell_child_and_preserves_output(tmp_path):
    marker = tmp_path / "child-finished"
    code = (
        "import pathlib, time; "
        "print('before timeout', flush=True); "
        "time.sleep(1.5); "
        f"pathlib.Path({str(marker)!r}).write_text('unexpected')"
    )
    cmd = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)} & wait"

    with pytest.raises(ToolError, match="did not finish in 1 seconds") as error:
        shell(cmd, timeout=1)

    assert "before timeout" in str(error.value)
    time.sleep(0.7)
    assert not marker.exists(), "The timed-out command left its child running."


def test_unapproved_command_does_not_create_a_process(monkeypatch):
    def unexpected_process(*args, **kwargs):
        pytest.fail("A command ran before the user approved it.")

    monkeypatch.setattr("peppermint.daemon.tools.shell.subprocess.Popen", unexpected_process)
    outcome = tools.call(
        "run_shell", {"cmd": "true", "purpose": "Check permission"},
        Context(task_id=0, require_approval=True),
    )
    assert isinstance(outcome, Confirm)
