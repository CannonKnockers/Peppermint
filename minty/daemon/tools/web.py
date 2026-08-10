"""Open a web address in the user's browser.

Opening a web page is not a change to the computer. Nothing is written, nothing
is installed, and the user sees the result immediately and can close it. So this
tool runs at once and never asks for approval.

The safety of the tool comes from what it refuses to accept, not from asking.
Only `http` and `https` reach the desktop. A `file:` address would read the
disk, `javascript:` would run code in the page the user is already logged into,
and `data:` would build a page out of thin air. None of those are a web address
in the sense the user means, so none are opened.

The address never touches a shell. It is passed to `xdg-open` as one argument in
a list, so a semicolon or a backtick inside it is only ever text. The check for
those characters is still there, because an address that contains them is
malformed and the user should be told, not have it quietly opened.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from urllib.parse import urlsplit

from minty.daemon.tools.registry import ToolError, tool

ALLOWED_SCHEMES = ("http", "https")

# Seconds to wait for the opener. xdg-open hands the address to the browser and
# returns; it does not wait for the page to load.
TIMEOUT = 20

# A scheme is a name, then a colon. `example.com:8080` looks the same at the
# start, so a colon followed only by digits is a port, not a scheme.
SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")
HOST_PORT = re.compile(r"^[A-Za-z0-9.\-]+:\d+(?:[/?#]|$)")

# Characters that never appear unencoded in a real web address, plus the shell
# separators, so a command cannot be smuggled in even as text.
#
# `?`, `&` and `#` are deliberately NOT here: a query string needs them
# (youtube.com/watch?v=x&t=42s). They are safe because the address is never
# handed to a shell -- see the module docstring. `;` is refused even though
# RFC 3986 permits it, because it is the shell's statement separator and a
# person opening a web site never types one; the cost is that old-style matrix
# parameters (`;jsessionid=`) are refused too.
FORBIDDEN_CHARACTERS = set(' \t\r\n\x00"\'<>\\^`{}|;')

# A local path, not a web address.
LOCAL_PREFIXES = ("/", "./", "../", "~")

# One label of a host name: letters, digits and hyphens.
LABEL = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-]*[A-Za-z0-9])?$")


def _reject(reason: str) -> ToolError:
    return ToolError(f"Minty did not open that address, because {reason}.")


def _check_characters(raw: str) -> None:
    for character in raw:
        if character in FORBIDDEN_CHARACTERS or ord(character) < 0x20:
            name = {" ": "a space", "\t": "a tab", "\n": "a new line",
                    "\r": "a carriage return"}.get(character, f"`{character}`")
            raise _reject(f"it contains {name}, which no web address contains")
    for sequence in ("$(", "&&", "||"):
        if sequence in raw:
            raise _reject(f"`{sequence}` looks like a command, not part of an address")


def _check_host(host: str) -> None:
    """A host is a dotted name, or `localhost`. Nothing else is a web site."""
    if not host:
        raise _reject("it has no site name")
    if host.endswith("."):
        host = host[:-1]
    labels = host.split(".")
    if len(labels) == 1 and host.lower() != "localhost":
        raise _reject(f"`{host}` is not a site name. Write it like example.com")
    for label in labels:
        if not LABEL.match(label):
            raise _reject(f"`{host}` is not a valid site name")


def normalise(raw: str) -> str:
    """Turn what the user said into one http or https address, or refuse it.

    `youtube.com` becomes `https://youtube.com`. Anything that is not a web
    address raises ToolError with a reason the model can read back to the user.
    """
    if not isinstance(raw, str):
        raise _reject("it is not text")

    candidate = raw.strip()
    if not candidate:
        raise _reject("it is empty")

    _check_characters(candidate)

    if candidate.startswith(LOCAL_PREFIXES):
        raise _reject("it is a path on this computer, not a web address")

    match = SCHEME.match(candidate)
    if match and not HOST_PORT.match(candidate):
        scheme = match.group(1).lower()
        if scheme not in ALLOWED_SCHEMES:
            raise _reject(
                f"`{scheme}:` is not a web address. Minty opens only http and https"
            )
    else:
        # No scheme. `youtube.com` and `example.com:8080` both land here.
        candidate = "https://" + candidate

    parts = urlsplit(candidate)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise _reject(f"`{parts.scheme}:` is not a web address")

    try:
        host = parts.hostname or ""
    except ValueError:
        raise _reject("its site name cannot be read")
    _check_host(host)

    return candidate


@tool(
    name="open_url",
    description=(
        "Open one web address in the user's browser. This is the correct tool whenever "
        "the user wants to see a web site, for example 'open youtube.com' or 'open my "
        "bank'. It runs at once and never asks for approval. Give one address per call; "
        "call the tool again for each further site. Write the address as the user said "
        "it (youtube.com) or in full (https://youtube.com). Only http and https work. "
        "Never use run_shell to open a web page."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The web address, for example youtube.com or https://x.com",
            },
        },
        "required": ["url"],
    },
)
def open_url(url: str):
    address = normalise(url)

    opener = shutil.which("xdg-open")
    if not opener:
        raise ToolError(
            "The `xdg-open` command is missing, so Minty cannot reach your browser."
        )

    try:
        proc = subprocess.run(
            [opener, address],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            # The browser must outlive a daemon restart.
            start_new_session=True,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"The browser did not answer within {TIMEOUT} seconds.")

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or f"exit code {proc.returncode}"
        raise ToolError(f"Minty could not open {address}: {detail}")

    return f"Opened {address} in your browser."
