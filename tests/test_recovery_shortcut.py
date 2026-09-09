"""Recovery shortcut tests use an in-memory desktop, never live GSettings."""

from __future__ import annotations

import copy
import json

import pytest

from peppermint.recovery.shortcut import (
    Binding, LIST_SETTING, RECOVERY_KEY, REPLACEMENT_KEY, Setting, build_plan,
    configure_shortcut, normalize_accelerator, restore_shortcut, slot_setting,
)


COMMAND = "/test/peppermint/.venv/bin/python -m peppermint.recovery.app"
LOGOUT = Setting("org.cinnamon.desktop.keybindings.media-keys", "logout")
TERMINAL = Setting("org.cinnamon.desktop.keybindings.media-keys", "terminal")


class Desktop:
    def __init__(self):
        self.defaults = {LIST_SETTING: [], LOGOUT: [RECOVERY_KEY], TERMINAL: ["<Primary><Alt>t"]}
        self.values = {}
        self.writes = []
        self.locked = set()
        self.fail_on = None
        self.before_write = None

    def read(self, setting):
        default = [] if setting.key == "binding" else ""
        return copy.deepcopy(self.values.get(setting, self.defaults.get(setting, default)))

    def user_value(self, setting):
        return copy.deepcopy(self.values.get(setting))

    def writable(self, setting):
        return setting not in self.locked

    def write(self, setting, value):
        if self.before_write:
            self.before_write(setting, value)
        if setting == self.fail_on:
            self.fail_on = None
            raise RuntimeError("simulated write failure")
        self.writes.append((setting, copy.deepcopy(value)))
        self.values[setting] = copy.deepcopy(value)

    def reset(self, setting):
        self.writes.append((setting, None))
        self.values.pop(setting, None)

    def sync(self):
        pass

    def bindings(self):
        result = [Binding(setting, setting.key, self.read(setting)) for setting in self.defaults
                  if setting != LIST_SETTING]
        for slot in self.read(LIST_SETTING):
            if slot == "__dummy__":
                continue
            setting = slot_setting(slot, "binding")
            result.append(Binding(setting, self.read(slot_setting(slot, "name")), self.read(setting),
                                  self.read(slot_setting(slot, "command")), slot))
        return result

    def custom(self, slot, name, command, bindings):
        self.values[LIST_SETTING] = [*self.read(LIST_SETTING), slot]
        for key, value in (("name", name), ("command", command), ("binding", bindings)):
            self.values[slot_setting(slot, key)] = value


@pytest.fixture
def desktop():
    return Desktop()


@pytest.mark.parametrize("accelerator", [
    "<Primary><Alt>Delete", "<ALT><Ctrl>Delete", "<Mod1><Ctl>delete", "Ctrl+Alt+Del",
    "<Control><Alt>0xffff",
])
def test_equivalent_accelerators_without_display(accelerator):
    assert normalize_accelerator(accelerator) == normalize_accelerator(RECOVERY_KEY)


@pytest.mark.parametrize("accelerator", ["<Control><Alt>KP_Delete", "<Release><Control><Alt>Delete", REPLACEMENT_KEY])
def test_distinct_key_or_modifiers_remain_distinct(accelerator):
    assert normalize_accelerator(accelerator) != normalize_accelerator(RECOVERY_KEY)


def test_plan_is_read_only_and_preserves_unrelated_shortcuts(desktop, tmp_path):
    desktop.custom("custom0", "Terminal", "terminal", ["<Super>t", "<Alt>F9"])
    desktop.values[LOGOUT] = ["XF86LogOff", "<Alt><Primary>Delete"]
    before = copy.deepcopy(desktop.values)
    backup = tmp_path / "absent" / "backup.json"
    plan = configure_shortcut(desktop, COMMAND, backup)
    assert desktop.values == before
    assert desktop.writes == []
    assert not backup.parent.exists()
    assert plan["slot"] == "custom1"
    assert plan["changes"][0]["after"] == ["XF86LogOff", REPLACEMENT_KEY]
    assert all(item["setting"] != {"schema": TERMINAL.schema, "key": TERMINAL.key, "path": ""}
               for item in plan["changes"])
    assert plan["moved"] == [{"action": "logout", "from": "Ctrl+Alt+Delete", "to": "Ctrl+Alt+Shift+Delete"}]


def test_second_custom_binding_blocks_replacement_before_any_change(desktop, tmp_path):
    desktop.custom("custom0", "Existing action", "existing", ["<Super>z", "<Shift><Primary><Mod1>Delete"])
    with pytest.raises(RuntimeError, match="already assigned to Existing action"):
        configure_shortcut(desktop, COMMAND, tmp_path / "backup.json", apply=True)
    assert desktop.writes == []
    assert not (tmp_path / "backup.json").exists()


def test_multiple_source_actions_fail_without_touching_bindings(desktop):
    desktop.custom("custom0", "Other action", "other", ["<Super>x", "<Alt><Ctl>Delete"])
    with pytest.raises(RuntimeError, match="multiple actions: logout, Other action"):
        build_plan(desktop, COMMAND)
    assert desktop.writes == []


def test_single_custom_source_is_moved_and_keeps_other_bindings(desktop, tmp_path):
    desktop.values[LOGOUT] = ["<Super>l"]
    desktop.custom("custom0", "Task manager", "task-manager", ["<Super>Escape", RECOVERY_KEY])
    configure_shortcut(desktop, COMMAND, tmp_path / "backup.json", apply=True)
    assert desktop.read(slot_setting("custom0", "binding")) == ["<Super>Escape", REPLACEMENT_KEY]
    assert desktop.read(slot_setting("custom0", "command")) == "task-manager"
    assert desktop.read(LOGOUT) == ["<Super>l"]


def test_free_source_does_not_move_or_require_replacement(desktop, tmp_path):
    desktop.values[LOGOUT] = [REPLACEMENT_KEY]
    result = configure_shortcut(desktop, COMMAND, tmp_path / "backup.json", apply=True)
    assert result["moved"] == []
    assert desktop.read(LOGOUT) == [REPLACEMENT_KEY]


def test_install_backs_up_originals_before_first_write_and_is_idempotent(desktop, tmp_path):
    backup = tmp_path / "backup.json"

    def check_backup(setting, value):
        snapshot = json.loads(backup.read_text())
        assert snapshot["status"] == "installing"
        assert snapshot["changes"][0]["before"] == [RECOVERY_KEY]
        assert snapshot["changes"][0]["before_user"] is None

    desktop.before_write = check_backup
    result = configure_shortcut(desktop, COMMAND, backup, apply=True)
    desktop.before_write = None
    assert result["status"] == "installed"
    assert desktop.read(LOGOUT) == [REPLACEMENT_KEY]
    assert desktop.read(slot_setting(result["slot"], "binding")) == [RECOVERY_KEY]
    assert backup.stat().st_mode & 0o777 == 0o600
    saved = backup.read_text()
    writes = len(desktop.writes)
    repeated = configure_shortcut(desktop, COMMAND, backup, apply=True)
    assert repeated["status"] == "already-installed"
    assert repeated["moved"] == result["moved"]
    assert len(desktop.writes) == writes
    assert backup.read_text() == saved


def test_restore_restores_original_values_and_default_state(desktop, tmp_path):
    desktop.custom("custom1", "Peppermint", "/test/peppermint", ["<Super>space"])
    desktop.values[LOGOUT] = ["<Primary><Alt>Delete", "XF86LogOff"]
    before = copy.deepcopy(desktop.values)
    backup = tmp_path / "backup.json"
    configure_shortcut(desktop, COMMAND, backup, apply=True)
    result = restore_shortcut(desktop, backup)
    assert result["status"] == "restored"
    assert desktop.values == before
    writes = len(desktop.writes)
    assert restore_shortcut(desktop, backup)["status"] == "restored"
    assert len(desktop.writes) == writes
    assert configure_shortcut(desktop, COMMAND, backup, apply=True)["status"] == "installed"


def test_restore_does_not_overwrite_later_user_changes(desktop, tmp_path):
    backup = tmp_path / "backup.json"
    configure_shortcut(desktop, COMMAND, backup, apply=True)
    desktop.values[LOGOUT] = ["<Super>l"]
    desktop.custom("custom2", "New user shortcut", "user-command", ["<Super>j"])
    result = restore_shortcut(desktop, backup)
    assert result["status"] == "partially-restored"
    assert LOGOUT.label in result["skipped"]
    assert LIST_SETTING.label in result["skipped"]
    assert desktop.read(LOGOUT) == ["<Super>l"]
    assert "custom2" in desktop.read(LIST_SETTING)
    assert desktop.read(slot_setting("custom2", "command")) == "user-command"


def test_inactive_custom_slot_with_saved_values_is_not_overwritten(desktop):
    desktop.values[slot_setting("custom0", "command")] = "inactive-user-command"
    plan = build_plan(desktop, COMMAND)
    assert plan["slot"] == "custom1"
    assert desktop.read(slot_setting("custom0", "command")) == "inactive-user-command"


@pytest.mark.parametrize("key,value", [("command", "user-command"), ("binding", ["<Super>r"])])
def test_repurposed_recovery_slot_remains_registered_on_restore(desktop, tmp_path, key, value):
    backup = tmp_path / "backup.json"
    configure_shortcut(desktop, COMMAND, backup, apply=True)
    desktop.values[slot_setting("custom0", key)] = value
    result = restore_shortcut(desktop, backup)
    assert result["status"] == "partially-restored"
    assert "custom0" in desktop.read(LIST_SETTING)
    assert desktop.read(slot_setting("custom0", key)) == value
    assert desktop.read(slot_setting("custom0", "name")) == "Peppermint Recovery"
    if key == "command":
        # The user's replacement command still owns Ctrl+Alt+Delete.
        assert desktop.read(LOGOUT) == [REPLACEMENT_KEY]
    else:
        assert desktop.read(LOGOUT) == [RECOVERY_KEY]


def test_existing_recovery_binding_keeps_other_accelerators(desktop):
    desktop.values[LOGOUT] = []
    desktop.custom("custom3", "My recovery shortcut", COMMAND, ["<Super>r"])
    plan = build_plan(desktop, COMMAND)
    assert plan["slot"] == "custom3"
    assert len(plan["changes"]) == 1
    assert plan["changes"][0]["after"] == ["<Super>r", RECOVERY_KEY]


def test_locked_setting_fails_before_backup_or_writes(desktop, tmp_path):
    desktop.locked.add(LOGOUT)
    with pytest.raises(RuntimeError, match="administratively locked"):
        configure_shortcut(desktop, COMMAND, tmp_path / "backup.json", apply=True)
    assert desktop.writes == []
    assert not (tmp_path / "backup.json").exists()


def test_failure_rolls_back_already_changed_settings(desktop, tmp_path):
    desktop.fail_on = slot_setting("custom0", "binding")
    original = copy.deepcopy(desktop.values)
    backup = tmp_path / "backup.json"
    with pytest.raises(RuntimeError, match="simulated write failure"):
        configure_shortcut(desktop, COMMAND, backup, apply=True)
    assert desktop.values == original
    assert json.loads(backup.read_text())["status"] == "restored"


def test_changed_settings_require_restoration_before_reinstall(desktop, tmp_path):
    backup = tmp_path / "backup.json"
    configure_shortcut(desktop, COMMAND, backup, apply=True)
    desktop.values[slot_setting("custom0", "binding")] = ["<Super>r"]
    writes = len(desktop.writes)
    with pytest.raises(RuntimeError, match="restoration before reinstalling"):
        configure_shortcut(desktop, COMMAND, backup, apply=True)
    assert len(desktop.writes) == writes


def test_concurrent_change_is_preserved_during_rollback(desktop, tmp_path):
    backup = tmp_path / "backup.json"

    def concurrent_change(setting, value):
        if setting == LOGOUT:
            desktop.values[slot_setting("custom0", "name")] = "User just claimed this slot"

    desktop.before_write = concurrent_change
    with pytest.raises(RuntimeError, match="changed during installation"):
        configure_shortcut(desktop, COMMAND, backup, apply=True)
    assert desktop.read(LOGOUT) == [RECOVERY_KEY]
    assert desktop.read(slot_setting("custom0", "name")) == "User just claimed this slot"
