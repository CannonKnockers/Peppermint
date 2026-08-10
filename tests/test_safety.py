"""The safety layer must never call a dangerous command safe.

Two kinds of test live here.

The **corpus tests** check one command each. They stop a known hole from
coming back.

The **property tests** check a rule over many commands at once. They matter
more. An earlier version of this suite passed only because the exact strings
it used happened to be caught by an unrelated rule, while whole classes of
attack went straight through. A property test cannot pass by accident in that
way.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from minty.daemon import safety
from tests.corpus_safety import OBFUSCATED, RISKY, SAFE

HOME = Path.home()


# --- the corpus -----------------------------------------------------------

@pytest.mark.parametrize("command,why", SAFE, ids=[c for c, _ in SAFE])
def test_read_only_commands_run_at_once(command, why):
    verdict = safety.classify_command(command)
    assert verdict.safe, f"{command!r} only reads ({why}), but Minty blocked it: {verdict.reason}"


@pytest.mark.parametrize("command,why", RISKY, ids=[c for c, _ in RISKY])
def test_dangerous_commands_wait_for_the_user(command, why):
    verdict = safety.classify_command(command)
    assert not verdict.safe, f"{command!r} {why}, but Minty called it safe"


@pytest.mark.parametrize("command,why", OBFUSCATED, ids=[c for c, _ in OBFUSCATED])
def test_a_different_spelling_does_not_hide_the_danger(command, why):
    verdict = safety.classify_command(command)
    assert not verdict.safe, f"{command!r} {why}, but Minty called it safe"


# --- properties -----------------------------------------------------------

SAFE_COMMANDS_ONLY = [c for c, _ in SAFE if not any(t in c for t in ("|", "&", ";"))]


@pytest.mark.parametrize("command", SAFE_COMMANDS_ONLY)
def test_appending_a_dangerous_command_always_wins(command):
    """No read-only command stays safe when a second command follows it."""
    for joiner in ("&&", ";", "||", "|"):
        attack = f"{command} {joiner} rm -rf /home/jesse"
        assert not safety.classify_command(attack).safe, f"{attack!r} passed"


@pytest.mark.parametrize("command", SAFE_COMMANDS_ONLY)
def test_prefixing_with_sudo_always_wins(command):
    assert not safety.classify_command(f"sudo {command}").safe


@pytest.mark.parametrize("command", SAFE_COMMANDS_ONLY)
def test_any_redirect_makes_a_command_risky(command):
    """A redirect writes. Nothing that writes is read-only."""
    for attack in (f"{command} > /home/jesse/out",
                   f"{command} >> /home/jesse/out",
                   f"{command} > ~/.bashrc"):
        assert not safety.classify_command(attack).safe, f"{attack!r} passed"


@pytest.mark.parametrize("command", SAFE_COMMANDS_ONLY)
def test_any_command_substitution_makes_a_command_risky(command):
    for attack in (f"{command} $(rm -rf ~)", f"{command} `rm -rf ~`"):
        assert not safety.classify_command(attack).safe, f"{attack!r} passed"


SECRET_PATHS = [
    "~/.ssh/id_rsa",
    "/home/jesse/.ssh/id_ed25519",
    "~/.gnupg/secring.gpg",
    "~/.aws/credentials",
    "/etc/shadow",
    "~/.netrc",
    "~/.git-credentials",
    "~/.mozilla/firefox/profile/logins.json",
    "~/.config/google-chrome/Default/Cookies",
    "~/.pypirc",
    "~/.npmrc",
    "/home/jesse/private.pem",
    "/home/jesse/server.key",
]


@pytest.mark.parametrize("path", SECRET_PATHS)
def test_no_reading_command_may_touch_a_secret(path):
    """The command name does not matter. The path does."""
    for reader in ("cat", "head -5", "tail", "grep x", "wc -l", "file", "less"):
        attack = f"{reader} {path}"
        assert not safety.classify_command(attack).safe, f"{attack!r} passed"


@pytest.mark.parametrize("path", SECRET_PATHS)
def test_the_file_tools_also_refuse_a_secret(path):
    assert not safety.classify_path_read(path).safe
    assert not safety.classify_path_write(path).safe


INTERPRETERS = ["python", "python3", "perl", "ruby", "node", "bash", "sh",
                "awk", "sed", "php", "lua", "zsh"]


@pytest.mark.parametrize("interpreter", INTERPRETERS)
def test_no_interpreter_is_ever_read_only(interpreter):
    """An interpreter runs any code, so its arguments cannot be judged."""
    for form in (f"{interpreter} script", f"{interpreter} -c 'print(1)'",
                 f"/usr/bin/{interpreter} x"):
        assert not safety.classify_command(form).safe, f"{form!r} passed"


PACKAGE_TOOLS = ["apt", "apt-get", "dpkg", "snap", "flatpak", "pip", "pip3",
                 "npm", "gem", "cargo", "pipx"]


@pytest.mark.parametrize("tool", PACKAGE_TOOLS)
def test_no_package_tool_is_ever_read_only(tool):
    for form in (f"{tool} install x", f"{tool} list", f"{tool} --help"):
        assert not safety.classify_command(form).safe, f"{form!r} passed"


NETWORK_TOOLS = ["curl", "wget", "ssh", "scp", "rsync", "nc", "ftp", "telnet"]


@pytest.mark.parametrize("tool", NETWORK_TOOLS)
def test_no_network_tool_is_read_only(tool):
    assert not safety.classify_command(f"{tool} example.com").safe


@pytest.mark.parametrize("flag", ["-exec", "-execdir", "-delete", "-ok", "-fprint"])
def test_find_is_only_safe_without_its_running_flags(flag):
    assert safety.classify_command("find ~ -name x").safe
    assert not safety.classify_command(f"find ~ -name x {flag} rm {{}} +").safe


def test_an_unknown_command_is_never_safe():
    """Default deny. Minty does not guess."""
    for command in ("frobnicate", "./x.sh", "/opt/thing", "definitely-not-real --x"):
        assert not safety.classify_command(command).safe


def test_every_safe_command_name_is_absent_from_the_risky_list():
    """A name in both lists would make the outcome depend on check order."""
    overlap = safety.SAFE_COMMANDS & safety.RISKY_COMMANDS
    assert not overlap, f"these names are in both lists: {sorted(overlap)}"


def test_the_policy_version_is_recorded():
    assert isinstance(safety.POLICY_VERSION, int)
    assert safety.POLICY_VERSION >= 2


# --- paths ----------------------------------------------------------------

def test_write_outside_home_is_risky():
    for path in ("/etc/hosts", "/usr/share/x", "/tmp/x", "/var/log/y"):
        assert not safety.classify_path_write(path).safe


def test_write_new_file_in_home_is_safe():
    assert safety.classify_path_write(str(HOME / "a-file-that-does-not-exist.txt")).safe


def test_replacing_a_file_is_risky(home_tmp):
    existing = home_tmp / "there.txt"
    existing.write_text("x")
    assert not safety.classify_path_write(str(existing)).safe


def test_hidden_config_write_is_risky():
    assert not safety.classify_path_write(str(HOME / ".config/foo/bar.conf")).safe
    assert not safety.classify_path_write(str(HOME / ".bashrc")).safe


def test_a_symlink_cannot_hide_the_real_target(home_tmp):
    """The path is resolved first, so a link out of the home directory is caught."""
    link = home_tmp / "innocent.txt"
    link.symlink_to("/etc/hosts")
    verdict = safety.classify_path_write(str(link))
    assert not verdict.safe
    assert "outside" in verdict.reason


def test_a_symlink_to_a_secret_is_caught(home_tmp):
    link = home_tmp / "notes.txt"
    link.symlink_to(HOME / ".ssh" / "id_rsa")
    assert not safety.classify_path_read(str(link)).safe


def test_dot_dot_cannot_escape_the_home_directory():
    assert not safety.classify_path_write(f"{HOME}/../../etc/hosts").safe


def test_a_plain_read_in_the_home_directory_is_safe():
    assert safety.classify_path_read(str(HOME / "notes.txt")).safe


# --- settings -------------------------------------------------------------

def test_desktop_settings_are_safe():
    for schema in ("org.cinnamon.desktop.interface", "org.cinnamon.theme",
                   "org.nemo.preferences", "org.gnome.desktop.background"):
        assert safety.classify_setting(schema).safe


def test_unknown_schema_needs_approval():
    for schema in ("org.gnome.system.proxy", "com.example.whatever",
                   "org.gnome.desktop.lockdown"):
        assert not safety.classify_setting(schema).safe
