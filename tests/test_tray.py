"""The panel icon must show the right state at a glance."""

from __future__ import annotations

import pytest

from peppermint.ui.tray import (
    ICON_ATTENTION,
    ICON_FAILED,
    ICON_IDLE,
    ICON_WORKING,
    pick_icon,
    shorten,
)


def task(task_id: int, status: str, idea: str = "do a thing") -> dict:
    return {"id": task_id, "status": status, "idea": idea}


def test_no_tasks_shows_the_idle_icon():
    icon, tooltip = pick_icon([])
    assert icon == ICON_IDLE
    assert "ready" in tooltip


def test_a_running_task_shows_the_working_icon():
    icon, tooltip = pick_icon([task(1, "running", "sort my files")])
    assert icon == ICON_WORKING
    assert "sort my files" in tooltip


def test_a_task_that_waits_shows_the_warning_icon():
    icon, tooltip = pick_icon([task(1, "awaiting-confirmation", "delete a file")])
    assert icon == ICON_ATTENTION
    assert "approval" in tooltip


def test_a_question_also_shows_the_warning_icon():
    icon, tooltip = pick_icon([task(1, "awaiting-input")])
    assert icon == ICON_ATTENTION
    assert "question" in tooltip


def test_waiting_wins_over_working():
    """The user must act first, so the warning icon wins."""
    icon, _ = pick_icon([task(1, "running"), task(2, "awaiting-confirmation")])
    assert icon == ICON_ATTENTION


def test_the_tooltip_counts_the_other_waiting_tasks():
    _, tooltip = pick_icon([
        task(1, "awaiting-confirmation"),
        task(2, "awaiting-input"),
        task(3, "awaiting-confirmation"),
    ])
    assert "+2 more" in tooltip


def test_a_failed_last_task_shows_the_error_icon():
    icon, tooltip = pick_icon([task(1, "failed")])
    assert icon == ICON_FAILED
    assert "failed" in tooltip


def test_finished_tasks_return_to_the_idle_icon():
    icon, _ = pick_icon([task(2, "done"), task(1, "done")])
    assert icon == ICON_IDLE


@pytest.mark.parametrize("status", ["queued", "planning", "running"])
def test_every_active_status_shows_working(status):
    icon, _ = pick_icon([task(1, status)])
    assert icon == ICON_WORKING


def test_shorten_keeps_short_text():
    assert shorten("short idea") == "short idea"


def test_shorten_cuts_long_text():
    result = shorten("x" * 100, limit=20)
    assert len(result) == 20
    assert result.endswith("…")


def test_shorten_folds_new_lines():
    assert shorten("two\n  lines") == "two lines"
