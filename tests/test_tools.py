"""The tools must behave the way a person expects."""

from __future__ import annotations

import pytest

from minty.daemon import tools
from minty.daemon.tools.registry import Ask, Confirm, Context, ToolError


@pytest.fixture
def ctx():
    return Context(task_id=1, db=None, approved=False)


@pytest.fixture
def approved():
    return Context(task_id=1, db=None, approved=True)


# --- move_file ------------------------------------------------------------

def test_move_into_a_missing_folder_makes_the_folder(home_tmp, ctx):
    """A file must never become the folder it was going into."""
    source = home_tmp / "notes.txt"
    source.write_text("hello")
    target = home_tmp / "Documents"

    result = tools.call("move_file", {"src": str(source), "dst": str(target)}, ctx)

    assert target.is_dir(), "the target must be a folder, not a renamed file"
    assert (target / "notes.txt").read_text() == "hello"
    assert not source.exists()
    assert "Documents/notes.txt" in str(result)


def test_two_moves_into_the_same_new_folder_both_work(home_tmp, ctx):
    """This is the file-sorting case that failed before."""
    target = home_tmp / "Images"
    for name in ("a.jpg", "b.png"):
        (home_tmp / name).write_text(name)

    for name in ("a.jpg", "b.png"):
        outcome = tools.call("move_file", {"src": str(home_tmp / name), "dst": str(target)}, ctx)
        assert not isinstance(outcome, Confirm), f"{name} should not need approval"

    assert sorted(p.name for p in target.iterdir()) == ["a.jpg", "b.png"]


def test_move_into_an_existing_folder(home_tmp, ctx):
    target = home_tmp / "Audio"
    target.mkdir()
    source = home_tmp / "song.mp3"
    source.write_text("x")

    tools.call("move_file", {"src": str(source), "dst": str(target)}, ctx)

    assert (target / "song.mp3").exists()


def test_rename_keeps_the_new_name(home_tmp, ctx):
    source = home_tmp / "old.txt"
    source.write_text("x")
    dest = home_tmp / "new.txt"

    tools.call("move_file", {"src": str(source), "dst": str(dest)}, ctx)

    assert dest.exists() and not source.exists()


def test_replacing_a_file_needs_approval(home_tmp, ctx):
    source = home_tmp / "a.txt"
    source.write_text("new")
    dest = home_tmp / "b.txt"
    dest.write_text("old")

    outcome = tools.call("move_file", {"src": str(source), "dst": str(dest)}, ctx)

    assert isinstance(outcome, Confirm)
    assert dest.read_text() == "old"


def test_moving_a_missing_file_is_an_error(home_tmp, ctx):
    with pytest.raises(ToolError):
        tools.call("move_file", {"src": str(home_tmp / "ghost"), "dst": str(home_tmp / "x")}, ctx)


# --- sort_folder ----------------------------------------------------------

RULES = {
    "Documents": ["pdf", "txt", "csv"],
    "Images": ["jpg", "png"],
    "Audio": ["mp3"],
    "Archives": ["zip"],
}


def test_sort_folder_moves_every_file(home_tmp, ctx):
    """The model misses a file when it moves them one by one. This tool must not."""
    names = ["report.pdf", "notes.txt", "data.csv", "slides.pdf",
             "photo1.jpg", "photo2.png", "holiday.jpg",
             "song.mp3", "archive.zip"]
    for name in names:
        (home_tmp / name).write_text(name)

    result = tools.call("sort_folder", {"path": str(home_tmp), "rules": RULES}, ctx)

    loose = [p.name for p in home_tmp.iterdir() if p.is_file()]
    assert loose == [], f"these files were left behind: {loose}"
    assert sorted(p.name for p in (home_tmp / "Documents").iterdir()) == [
        "data.csv", "notes.txt", "report.pdf", "slides.pdf"]
    assert len(list((home_tmp / "Images").iterdir())) == 3
    assert "Sorted 9 files" in str(result)


def test_sort_folder_leaves_unknown_endings_alone(home_tmp, ctx):
    (home_tmp / "a.pdf").write_text("x")
    (home_tmp / "mystery.xyz").write_text("x")

    result = tools.call("sort_folder", {"path": str(home_tmp), "rules": RULES}, ctx)

    assert (home_tmp / "mystery.xyz").exists()
    assert "mystery.xyz" in str(result)
    assert "had no rule: xyz" in str(result), "the tool must name the endings it could not place"


def test_sort_folder_can_collect_the_rest(home_tmp, ctx):
    (home_tmp / "mystery.xyz").write_text("x")

    tools.call("sort_folder",
               {"path": str(home_tmp), "rules": RULES, "other_folder": "Other"}, ctx)

    assert (home_tmp / "Other" / "mystery.xyz").exists()


def test_sort_folder_does_not_touch_subfolders(home_tmp, ctx):
    (home_tmp / "keep").mkdir()
    (home_tmp / "keep" / "inner.pdf").write_text("x")
    (home_tmp / "outer.pdf").write_text("x")

    tools.call("sort_folder", {"path": str(home_tmp), "rules": RULES}, ctx)

    assert (home_tmp / "keep" / "inner.pdf").exists()
    assert (home_tmp / "Documents" / "outer.pdf").exists()


def test_sort_folder_keeps_hidden_files(home_tmp, ctx):
    (home_tmp / ".secret.txt").write_text("x")

    tools.call("sort_folder", {"path": str(home_tmp), "rules": RULES}, ctx)

    assert (home_tmp / ".secret.txt").exists()


def test_sort_folder_does_not_replace_a_file(home_tmp, ctx):
    (home_tmp / "Documents").mkdir()
    (home_tmp / "Documents" / "a.txt").write_text("original")
    (home_tmp / "a.txt").write_text("new")

    result = tools.call("sort_folder", {"path": str(home_tmp), "rules": RULES}, ctx)

    assert (home_tmp / "Documents" / "a.txt").read_text() == "original"
    assert (home_tmp / "a.txt").exists()
    assert "already in Documents" in str(result)


def test_sort_folder_accepts_endings_with_a_dot(home_tmp, ctx):
    (home_tmp / "a.pdf").write_text("x")
    tools.call("sort_folder", {"path": str(home_tmp), "rules": {"Docs": [".PDF"]}}, ctx)
    assert (home_tmp / "Docs" / "a.pdf").exists()


def test_sort_folder_needs_rules(home_tmp, ctx):
    with pytest.raises(ToolError):
        tools.call("sort_folder", {"path": str(home_tmp), "rules": {}}, ctx)


def test_sort_folder_outside_home_asks_first(ctx):
    outcome = tools.call("sort_folder", {"path": "/tmp", "rules": RULES}, ctx)
    assert isinstance(outcome, Confirm)


# --- make_dir -------------------------------------------------------------

def test_make_dir_makes_parents(home_tmp, ctx):
    target = home_tmp / "a" / "b" / "c"
    tools.call("make_dir", {"path": str(target)}, ctx)
    assert target.is_dir()


def test_make_dir_is_quiet_when_it_exists(home_tmp, ctx):
    result = tools.call("make_dir", {"path": str(home_tmp)}, ctx)
    assert "already exists" in str(result)


# --- shell ----------------------------------------------------------------

def test_safe_shell_runs(ctx):
    result = tools.call("run_shell", {"cmd": "echo hello", "purpose": "test"}, ctx)
    assert "hello" in str(result)


def test_risky_shell_asks_first(ctx):
    outcome = tools.call("run_shell", {"cmd": "rm -rf /tmp/nothing", "purpose": "test"}, ctx)
    assert isinstance(outcome, Confirm)
    assert "rm -rf" in outcome.description


def test_shell_reports_a_bad_exit_code(ctx):
    result = tools.call("run_shell", {"cmd": "ls /this/does/not/exist", "purpose": "test"}, ctx)
    assert "exit code" in str(result)


# --- files ----------------------------------------------------------------

def test_read_and_list(home_tmp, ctx):
    (home_tmp / "a.txt").write_text("line one\nline two")
    listing = tools.call("list_dir", {"path": str(home_tmp)}, ctx)
    assert "a.txt" in str(listing)
    content = tools.call("read_file", {"path": str(home_tmp / "a.txt")}, ctx)
    assert "line two" in str(content)


def test_read_file_refuses_a_directory(home_tmp, ctx):
    with pytest.raises(ToolError):
        tools.call("read_file", {"path": str(home_tmp)}, ctx)


def test_search_files_finds_by_pattern(home_tmp, ctx):
    (home_tmp / "one.pdf").write_text("x")
    (home_tmp / "two.txt").write_text("x")
    result = tools.call("search_files", {"pattern": "*.pdf", "root": str(home_tmp)}, ctx)
    assert "one.pdf" in str(result) and "two.txt" not in str(result)


def test_delete_always_asks(home_tmp, ctx):
    victim = home_tmp / "x.txt"
    victim.write_text("x")
    outcome = tools.call("delete_file", {"path": str(victim)}, ctx)
    assert isinstance(outcome, Confirm)
    assert victim.exists()


# --- the registry ---------------------------------------------------------

def test_unknown_tool_names_the_real_tools(ctx):
    with pytest.raises(ToolError) as exc:
        tools.call("fly_to_the_moon", {}, ctx)
    assert "list_dir" in str(exc.value)


def test_unknown_argument_is_reported(ctx):
    with pytest.raises(ToolError) as exc:
        tools.call("list_dir", {"path": "/tmp", "colour": "blue"}, ctx)
    assert "colour" in str(exc.value)


def test_missing_argument_is_reported(ctx):
    with pytest.raises(ToolError) as exc:
        tools.call("move_file", {"src": "/tmp/a"}, ctx)
    assert "dst" in str(exc.value)


def test_ask_user_returns_a_question(ctx):
    outcome = tools.call("ask_user", {"question": "Which folder?"}, ctx)
    assert isinstance(outcome, Ask)


def test_settings_read_works(ctx):
    result = tools.call("gsettings_get",
                        {"schema": "org.cinnamon.desktop.interface", "key": "gtk-theme"}, ctx)
    assert str(result).strip().startswith("'")


def test_unknown_schema_write_asks_first(ctx):
    outcome = tools.call("gsettings_set",
                         {"schema": "com.example.made.up", "key": "x", "value": "1"}, ctx)
    assert isinstance(outcome, Confirm)


def test_apt_install_always_asks(ctx):
    outcome = tools.call("apt_install", {"packages": ["htop"]}, ctx)
    assert isinstance(outcome, Confirm)


def test_apt_install_rejects_a_bad_name(ctx):
    with pytest.raises(ToolError):
        tools.call("apt_install", {"packages": ["htop; rm -rf ~"]}, ctx)


def test_schedule_rejects_a_bad_cron_expression(ctx):
    with pytest.raises(ToolError):
        tools.call("schedule", {"cron_expr": "every monday", "command": "echo hi"}, ctx)


def test_schedule_asks_before_it_writes(ctx):
    outcome = tools.call("schedule", {"cron_expr": "0 9 * * 1", "command": "echo hi"}, ctx)
    assert isinstance(outcome, Confirm)
