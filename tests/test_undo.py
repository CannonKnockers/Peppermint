"""Recording an old value is not enough. Peppermint must put it back."""

from __future__ import annotations

import subprocess

import pytest

from peppermint.daemon import undo
from peppermint.daemon.db import Database


@pytest.fixture
def db():
    return Database(":memory:")


# --- moves ----------------------------------------------------------------

def test_a_moved_file_goes_back(home_tmp):
    source = home_tmp / "notes.txt"
    source.write_text("mine")
    folder = home_tmp / "Documents"
    folder.mkdir()
    moved = folder / "notes.txt"
    source.rename(moved)

    result = undo.revert({"kind": "move", "target": str(moved), "old_value": str(source)})

    assert result.ok, result.message
    assert source.read_text() == "mine"
    assert not moved.exists()


def test_a_move_back_makes_the_old_folder_again(home_tmp):
    old_folder = home_tmp / "gone"
    old_folder.mkdir()
    source = old_folder / "x.txt"
    source.write_text("x")
    moved = home_tmp / "x.txt"
    source.rename(moved)
    old_folder.rmdir()

    result = undo.revert({"kind": "move", "target": str(moved), "old_value": str(source)})

    assert result.ok, result.message
    assert source.exists()


def test_a_move_back_refuses_to_replace_a_new_file(home_tmp):
    """Something new sits where the file used to be. Peppermint must not overwrite it."""
    original = home_tmp / "notes.txt"
    moved = home_tmp / "Documents" / "notes.txt"
    moved.parent.mkdir()
    moved.write_text("moved")
    original.write_text("something new")

    result = undo.revert({"kind": "move", "target": str(moved), "old_value": str(original)})

    assert not result.ok
    assert original.read_text() == "something new", "the new file must survive"


def test_a_move_back_reports_a_missing_file(home_tmp):
    result = undo.revert({"kind": "move", "target": str(home_tmp / "ghost"),
                          "old_value": str(home_tmp / "was")})
    assert not result.ok
    assert "not there" in result.message


# --- file contents --------------------------------------------------------

def test_file_contents_are_restored(home_tmp):
    target = home_tmp / "config.txt"
    target.write_text("new text")

    result = undo.revert({"kind": "file", "target": str(target), "old_value": "old text"})

    assert result.ok, result.message
    assert target.read_text() == "old text"


def test_restoring_a_file_leaves_no_temporary_behind(home_tmp):
    target = home_tmp / "config.txt"
    target.write_text("new")
    undo.revert({"kind": "file", "target": str(target), "old_value": "old"})
    leftovers = [p.name for p in home_tmp.iterdir() if "peppermint-undo" in p.name]
    assert leftovers == []


# --- settings -------------------------------------------------------------

def test_a_setting_goes_back():
    """This test changes a real desktop setting and puts it back."""
    schema, key = "org.cinnamon.desktop.interface", "font-name"
    before = subprocess.run(["gsettings", "get", schema, key],
                            capture_output=True, text=True).stdout.strip()
    subprocess.run(["gsettings", "set", schema, key, "'Sans 11'"], capture_output=True)
    try:
        result = undo.revert({"kind": "gsettings", "target": f"{schema} {key}",
                              "old_value": before})
        assert result.ok, result.message
        after = subprocess.run(["gsettings", "get", schema, key],
                               capture_output=True, text=True).stdout.strip()
        assert after == before
    finally:
        subprocess.run(["gsettings", "set", schema, key, before], capture_output=True)


def test_a_broken_setting_record_is_reported():
    result = undo.revert({"kind": "gsettings", "target": "nonsense", "old_value": "x"})
    assert not result.ok


def test_an_empty_old_value_is_refused():
    result = undo.revert({"kind": "gsettings", "target": "org.a.b key", "old_value": ""})
    assert not result.ok
    assert "no earlier value" in result.message


# --- unknown kinds --------------------------------------------------------

def test_an_unknown_change_is_not_guessed():
    result = undo.revert({"kind": "sorcery", "target": "x", "old_value": "y"})
    assert not result.ok
    assert "does not know how" in result.message


# --- the record book ------------------------------------------------------

def test_a_change_can_only_be_put_back_once(db):
    db.add_task("change something")
    db.record_undo(1, "gsettings", "org.a.b key", "'old'")
    record = db.last_undo()[0]

    assert db.mark_undone(record["id"]) is True
    assert db.mark_undone(record["id"]) is False, "a second undo must do nothing"


def test_undone_changes_leave_the_list(db):
    db.add_task("change something")
    db.record_undo(1, "gsettings", "org.a.b key", "'old'")
    record = db.last_undo()[0]
    db.mark_undone(record["id"])

    assert db.last_undo() == []
    assert len(db.last_undo(include_done=True)) == 1


def test_undo_can_be_read_for_one_task(db):
    db.add_task("first")
    db.add_task("second")
    db.record_undo(1, "gsettings", "a b", "'x'")
    db.record_undo(2, "gsettings", "c d", "'y'")

    assert len(db.last_undo(task_id=1)) == 1
    assert db.last_undo(task_id=1)[0]["target"] == "a b"


def test_the_newest_change_comes_first(db):
    db.add_task("many changes")
    db.record_undo(1, "gsettings", "first key", "'1'")
    db.record_undo(1, "gsettings", "second key", "'2'")

    records = db.last_undo()
    assert records[0]["target"] == "second key", \
        "the newest change must go back first, or the earlier one will not match"


def test_every_description_is_readable():
    for record in (
        {"kind": "gsettings", "target": "org.a.b key", "old_value": "'Mint-Y'"},
        {"kind": "move", "target": "/home/a/b.txt", "old_value": "/home/b.txt"},
        {"kind": "file", "target": "/home/a/c.txt", "old_value": "old"},
    ):
        line = undo.describe(record)
        assert line and not line.startswith("undo "), f"no plain wording for {record['kind']}"
