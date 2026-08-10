"""Risk classification for actions.

The model never sets its own risk class. This module does, and the tool layer
obeys it.

The rule is default-deny. An action is safe only when every check below agrees
that it is safe. Anything else waits for the user.

A command name alone is not enough evidence. `find` is a read-only command
until you add `-delete`. `echo` is harmless until you add `>`. So a command
passes only when the shell text, the command name, the subcommand, the flags,
and the paths all pass.

POLICY VERSION: raise this when a rule changes. The number is recorded with
every task, so an old log can be read against the rules of its own time.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from minty.common.models import Risk, Verdict

POLICY_VERSION = 2

HOME = Path.home()

# --- Rule 1: the shape of the command line ---------------------------------
# A redirect writes. A substitution runs a second command. Neither can be
# read-only, whatever the command name says.
#
# These are found in the *token* list, not in the raw text. A regex over raw
# text cannot tell `egrep 'a|b' file`, where the pipe is part of a search
# pattern, from `ls | rm`, where it joins two commands.

SEPARATORS = {";", "&&", "||", "|", "&", "\n", ";;"}

REDIRECTS = {">", ">>", "<", "<<", "<<<", "<>", ">|", "&>", "&>>", ">&", "<&",
             "<(", ">("}

# Traps that survive tokenizing and must be found in the raw text.
RAW_TRAPS = [
    (re.compile(r"`"), "runs a command inside a command"),
    (re.compile(r"\\x[0-9a-fA-F]{2}|\\0[0-7]{2}"), "hides text in escape codes"),
    (re.compile(r":\s*\(\s*\)\s*\{"), "is a fork bomb"),
]

# --- Rule 2: commands that are never read-only -----------------------------

RISKY_COMMANDS = {
    # privilege
    "sudo", "pkexec", "su", "doas", "runuser", "setpriv",
    # deletion and disks
    "rm", "rmdir", "shred", "unlink", "srm", "wipe",
    "dd", "mkfs", "fdisk", "parted", "mount", "umount", "swapoff", "sync",
    # permissions
    "chmod", "chown", "chgrp", "setfacl", "chattr", "umask",
    # processes and power
    "kill", "killall", "pkill", "reboot", "shutdown", "halt", "poweroff",
    "systemctl", "service", "initctl", "loginctl",
    # scheduling
    "crontab", "at", "batch", "anacron",
    # packages
    "apt", "apt-get", "aptitude", "dpkg", "dpkg-reconfigure", "add-apt-repository",
    "snap", "flatpak", "pip", "pip3", "pipx", "npm", "yarn", "gem", "cargo",
    # accounts and network configuration
    "usermod", "useradd", "userdel", "passwd", "groupadd", "visudo", "gpasswd",
    "iptables", "ufw", "nft", "modprobe", "insmod", "rmmod", "sysctl", "ip",
    # anything that fetches or sends
    "curl", "wget", "nc", "ncat", "netcat", "ssh", "scp", "sftp", "rsync",
    "ftp", "telnet", "aria2c", "yt-dlp", "youtube-dl",
    # interpreters: they run any code at all
    "python", "python2", "python3", "perl", "ruby", "node", "nodejs", "php",
    "lua", "tclsh", "awk", "gawk", "mawk", "sed", "ed", "vi", "vim", "nano",
    "emacs", "bash", "sh", "zsh", "dash", "ksh", "fish", "csh",
    "eval", "exec", "source", ".", "xargs", "parallel", "watch", "timeout",
    "nohup", "setsid", "env", "nice", "ionice", "strace", "ltrace", "gdb",
    # writes files as a normal function
    "tee", "tar", "zip", "unzip", "gzip", "gunzip", "7z", "cp", "mv", "install",
    "ln", "mkdir", "touch", "truncate", "split", "patch", "make", "cmake",
    # leaks the environment, which holds tokens
    "printenv", "set", "export", "declare",
}

# --- Rule 3: commands that only read --------------------------------------

SAFE_COMMANDS = {
    "ls", "cat", "head", "tail", "wc", "file", "stat", "du", "df",
    "grep", "egrep", "fgrep", "rg", "find", "locate", "which", "whereis", "type",
    "basename", "dirname", "realpath", "readlink", "sort", "uniq", "cut", "column",
    "ps", "pstree", "free", "uptime", "vmstat", "iostat", "lsof",
    "uname", "hostname", "hostnamectl", "lsb_release", "inxi", "arch",
    "date", "cal", "echo", "printf", "true", "false", "id", "whoami", "groups",
    "lspci", "lsusb", "lsblk", "lscpu", "lsmod", "dmidecode", "nvidia-smi",
    "xdg-mime", "xdg-user-dir", "xrandr", "xdpyinfo",
    "apt-cache", "dpkg-query", "apt-mark", "locale", "timedatectl", "getent",
    "md5sum", "sha256sum", "cksum", "diff", "cmp", "comm", "seq", "yes",
}

# --- Rule 4: commands that read only with the right subcommand -------------

SAFE_SUBCOMMANDS = {
    "gsettings": {"get", "list-schemas", "list-keys", "list-recursively", "list-children",
                  "range", "describe", "monitor"},
    "dconf": {"read", "list", "dump", "watch"},
    "git": {"status", "log", "diff", "show", "branch", "remote", "rev-parse",
            "describe", "blame", "shortlog", "tag", "ls-files"},
    "journalctl": {"-n", "--lines", "-u", "--unit", "-e", "--since", "--no-pager"},
    "systemd-analyze": {"blame", "critical-chain", "time"},
    "pactl": {"list", "info", "stat"},
    "nmcli": {"device", "connection", "general", "radio"},
}

# --- Rule 5: flags that turn a read-only command into a writing one --------

FORBIDDEN_FLAGS = {
    "find": {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint",
             "-fprintf", "-fls", "-printf"},
    "sort": {"-o", "--output"},
    "grep": {"-f", "--file"},          # reads a file of patterns; also a leak path
    "nvidia-smi": {"-pl", "--power-limit", "-ac", "-rac", "-pm", "-e", "-r",
                   "--gpu-reset", "-lgc", "-lmc"},
    "date": {"-s", "--set"},
    "diff": {"-D", "--ifdef"},
    "ps": {"-o"},                       # a custom format can leak a full command line
    "nmcli": {"-a", "--ask", "-s", "--show-secrets"},
    "xrandr": {"--output", "--mode", "--auto", "--rotate", "--size"},
    "getent": set(),
    "locate": {"-d", "--database"},
}

# --- Rule 6: paths that hold secrets --------------------------------------
# These stay out of the model's context, whatever command asks for them.

SECRET_MARKERS = (
    "/.ssh", "/.gnupg", "/.aws", "/.azure", "/.kube", "/.docker",
    "/.config/keyring", "/.local/share/keyrings", "/.mozilla", "/.thunderbird",
    "/.config/google-chrome", "/.config/chromium", "/.config/BraveSoftware",
    "/.netrc", "/.pgpass", "/.git-credentials", "/.npmrc", "/.pypirc",
    "shadow", "gshadow", "sudoers", "/etc/ssl/private", "/.password-store",
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", ".pem", ".key", ".keystore",
    "secret", "credential", "passwd.db",
)

# Desktop settings Minty may change without asking. All are easy to undo.
SAFE_SCHEMA_PREFIXES = (
    "org.cinnamon",
    "org.gnome.desktop.interface",
    "org.gnome.desktop.background",
    "org.gnome.desktop.screensaver",
    "org.gnome.desktop.wm.preferences",
    "org.gnome.desktop.peripherals",
    "org.x.apps",
    "org.nemo",
    "org.gnome.terminal",
)


def looks_secret(text: str) -> bool:
    """True when the text points at something that can hold a secret."""
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in SECRET_MARKERS)


def _strip_env_prefix(tokens: list[str]) -> list[str]:
    """Drop leading VAR=value assignments."""
    out = list(tokens)
    while out and "=" in out[0] and not out[0].startswith("-") and "/" not in out[0].split("=")[0]:
        out.pop(0)
    return out


def _first_argument(tokens: list[str], command: str) -> str:
    """The first real argument, ignoring the global flags of some commands."""
    for token in tokens[1:]:
        if command == "journalctl":
            return token
        if not token.startswith("-"):
            return token
    return tokens[1] if len(tokens) > 1 else ""


def tokenize(command: str) -> list[str]:
    """Split a command line the way a shell does, respecting quotes.

    Raises ValueError when the quoting is broken.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def classify_tokens(tokens: list[str]) -> Verdict:
    """Classify one command given as tokens, with no separator inside."""
    tokens = _strip_env_prefix([t for t in tokens if t])
    if not tokens:
        return Verdict(Risk.SAFE, "no command")
    if not tokens:
        return Verdict(Risk.SAFE, "no command")

    name = os.path.basename(tokens[0])
    if name.startswith("-"):
        return Verdict(Risk.RISKY, "the command starts with a flag, not a name")

    if name in RISKY_COMMANDS:
        return Verdict(Risk.RISKY, f"`{name}` can change the system or run any code")

    for token in tokens[1:]:
        if looks_secret(token):
            return Verdict(Risk.RISKY, f"`{token}` can hold a password or a key")

    forbidden = FORBIDDEN_FLAGS.get(name)
    if forbidden:
        for token in tokens[1:]:
            flag = token.split("=", 1)[0]
            if flag in forbidden:
                return Verdict(Risk.RISKY, f"`{name} {flag}` does more than read")

    if name in SAFE_SUBCOMMANDS:
        first = _first_argument(tokens, name)
        if first in SAFE_SUBCOMMANDS[name]:
            return Verdict(Risk.SAFE, f"`{name} {first}` only reads")
        return Verdict(Risk.RISKY, f"`{name} {first or ''}`.strip() can change the system")

    if name in SAFE_COMMANDS:
        return Verdict(Risk.SAFE, f"`{name}` only reads")

    return Verdict(Risk.RISKY, f"`{name}` is not on the read-only list")


def classify_segment(segment: str) -> Verdict:
    """Classify one command written as text. Kept for direct use and tests."""
    if not segment.strip():
        return Verdict(Risk.SAFE, "empty command")
    try:
        return classify_tokens(tokenize(segment))
    except ValueError:
        return Verdict(Risk.RISKY, "the command has unbalanced quotes")


def classify_command(command: str) -> Verdict:
    """Classify a full shell command. Every part must be safe."""
    if not command or not command.strip():
        return Verdict(Risk.RISKY, "the command is empty")
    if "\x00" in command:
        return Verdict(Risk.RISKY, "the command holds a null character")

    for pattern, reason in RAW_TRAPS:
        if pattern.search(command):
            return Verdict(Risk.RISKY, f"the command {reason}")

    # A new line separates two commands, but the tokenizer treats it as plain
    # space. Split the lines first, so a second line cannot hide.
    lines = [line for line in command.splitlines() if line.strip()]
    if len(lines) > 1:
        for line in lines:
            verdict = classify_command(line)
            if not verdict.safe:
                return verdict
        return Verdict(Risk.SAFE, "every line only reads")

    try:
        tokens = tokenize(command)
    except ValueError:
        return Verdict(Risk.RISKY, "the command has unbalanced quotes")

    for index, token in enumerate(tokens):
        if token in REDIRECTS:
            return Verdict(Risk.RISKY, "the command writes with a redirect")
        # `$(` arrives as the two tokens `$` and `(`.
        if token == "(" or (token == "$" and index + 1 < len(tokens)):
            return Verdict(Risk.RISKY, "the command runs a command inside a command")

    segment: list[str] = []
    for token in tokens + [";"]:
        if token in SEPARATORS:
            verdict = classify_tokens(segment)
            if not verdict.safe:
                return verdict
            segment = []
        else:
            segment.append(token)

    return Verdict(Risk.SAFE, "every part of the command only reads")


# --- paths ----------------------------------------------------------------

def resolve(path: str) -> Path:
    """Expand and resolve a path, following every symlink."""
    return Path(os.path.expandvars(str(path))).expanduser().resolve()


def _inside_home(resolved: Path) -> bool:
    return resolved == HOME or HOME in resolved.parents


def classify_path_write(path: str) -> Verdict:
    """Classify a write. The path is resolved first, so a symlink cannot hide."""
    try:
        resolved = resolve(path)
    except (OSError, RuntimeError, ValueError):
        return Verdict(Risk.RISKY, "the path cannot be read")

    if looks_secret(str(resolved)):
        return Verdict(Risk.RISKY, "the path can hold a password or a key")
    if not _inside_home(resolved):
        return Verdict(Risk.RISKY, "the path is outside your home directory")

    parts = resolved.relative_to(HOME).parts
    if any(part.startswith(".") for part in parts[:-1]):
        return Verdict(Risk.RISKY, "the path is inside a hidden configuration directory")
    if resolved.exists():
        return Verdict(Risk.RISKY, "the file exists and the write replaces it")
    return Verdict(Risk.SAFE, "the write makes a new file in your home directory")


def classify_path_read(path: str) -> Verdict:
    """Classify a read. A read is safe unless the path can hold a secret."""
    try:
        resolved = resolve(path)
    except (OSError, RuntimeError, ValueError):
        return Verdict(Risk.RISKY, "the path cannot be read")
    if looks_secret(str(resolved)) or looks_secret(str(path)):
        return Verdict(Risk.RISKY, "the path can hold a password or a key")
    return Verdict(Risk.SAFE, "a read does not change anything")


def classify_setting(schema: str) -> Verdict:
    """Classify a gsettings write."""
    if schema.startswith(SAFE_SCHEMA_PREFIXES):
        return Verdict(Risk.SAFE, "this desktop setting is easy to undo")
    return Verdict(Risk.RISKY, f"the schema `{schema}` is not a known desktop setting")
