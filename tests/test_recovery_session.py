"""All session actions are mocked; this suite never changes the host session."""

from __future__ import annotations

import subprocess

import pytest

from peppermint.recovery import session


class Backend:
    def __init__(self):
        self.calls = []
        self.runs = []
        self.session_capabilities = (True, True, True, False, True, False, True)
        self.screensaver = True
        self.activatable = []
        self.allow_lock = True
        self.has_dm_tool = True
        self.errors = {}

    def call(self, **arguments):
        self.calls.append(arguments)
        method = arguments["method"]
        if method in self.errors:
            raise self.errors[method]
        if method == "GetCapabilities":
            return (self.session_capabilities,)
        if method == "NameHasOwner":
            return (self.screensaver,)
        if method == "ListActivatableNames":
            return (self.activatable,)
        return ()

    def executable(self, path):
        assert path == "/usr/bin/dm-tool"
        return self.has_dm_tool

    def lock_allowed(self):
        return self.allow_lock

    def run(self, arguments, *, timeout_ms):
        self.runs.append((arguments, timeout_ms))
        if "run" in self.errors:
            raise self.errors["run"]


@pytest.fixture
def backend():
    return Backend()


def test_capability_mapping_is_read_only_and_omits_unsupported_hibernate(backend):
    assert session.capabilities(backend) == {
        "lock": True, "switch_user": True, "logout": True, "restart": True,
        "shutdown": True, "suspend": True, "hibernate": False,
    }
    assert [call["method"] for call in backend.calls] == ["GetCapabilities", "NameHasOwner"]
    assert backend.runs == []
    assert backend.calls[0]["interface"] == session.DIALOG_INTERFACE
    assert all(0 < call["timeout_ms"] <= 3000 for call in backend.calls)


@pytest.mark.parametrize("value", [(True, False), (True,) * 6 + (1,), (), "invalid"])
def test_malformed_capabilities_are_unavailable(backend, value):
    backend.session_capabilities = value
    result = session.capabilities(backend)
    assert result["lock"]
    assert not any(result[action] for action in session.ACTIONS if action != "lock")


def test_unavailable_session_manager_does_not_invent_supported_actions(backend):
    backend.errors["GetCapabilities"] = TimeoutError("service stalled")
    result = session.capabilities(backend)
    assert result["lock"]
    assert not result["logout"]
    assert not result["shutdown"]


@pytest.mark.parametrize("attribute", ["screensaver", "allow_lock"])
def test_lock_unavailable_also_disables_switching_to_unlocked_session(backend, attribute):
    setattr(backend, attribute, False)
    result = session.capabilities(backend)
    assert not result["lock"]
    assert not result["switch_user"]
    assert result["logout"]


def test_missing_dm_tool_disables_only_user_switching(backend):
    backend.has_dm_tool = False
    result = session.capabilities(backend)
    assert not result["switch_user"]
    assert result["lock"]


def test_idle_screensaver_is_available_without_starting_it(backend):
    backend.screensaver = False
    backend.activatable = [session.SCREEN_NAME]
    result = session.capabilities(backend)
    assert result["lock"]
    assert result["switch_user"]
    assert [call["method"] for call in backend.calls] == ["GetCapabilities", "NameHasOwner", "ListActivatableNames"]
    assert not any(call["auto_start"] for call in backend.calls)


@pytest.mark.parametrize("action,method,signature,arguments", [
    ("logout", "Logout", "(u)", (0,)),
    ("restart", "Reboot", None, ()),
    ("shutdown", "Shutdown", None, ()),
])
def test_end_session_actions_keep_native_confirmation_and_inhibitors(backend, action, method, signature, arguments):
    result = session.request(action, backend)
    call = backend.calls[-1]
    assert call["bus"] == "session"
    assert call["destination"] == session.SESSION_NAME
    assert call["path"] == session.SESSION_PATH
    assert call["interface"] == session.SESSION_NAME
    assert call["method"] == method
    assert call["signature"] == signature
    assert call["arguments"] == arguments
    assert result["status"] == "requested"
    assert "confirmation" in result["message"]
    assert "block" in result["message"]
    assert backend.runs == []


def test_lock_requests_cinnamon_screensaver_with_empty_message(backend):
    result = session.request("lock", backend)
    call = backend.calls[-1]
    assert call["destination"] == session.SCREEN_NAME
    assert call["path"] == session.SCREEN_PATH
    assert call["interface"] == session.SCREEN_NAME
    assert call["method"] == "Lock"
    assert call["signature"] == "(s)"
    assert call["arguments"] == ("",)
    assert call["auto_start"] is True
    assert result["status"] == "requested"


def test_switch_user_locks_before_fixed_greeter_command(backend):
    result = session.request("switch_user", backend)
    assert backend.calls[-1]["method"] == "Lock"
    assert backend.runs[0][0] == ["/usr/bin/dm-tool", "switch-to-greeter"]
    assert 0 < backend.runs[0][1] <= 3000
    assert result["status"] == "requested"


def test_switch_user_does_not_proceed_if_lock_request_fails(backend):
    backend.errors["Lock"] = RuntimeError("screensaver unavailable")
    result = session.request("switch_user", backend)
    assert result["status"] == "error"
    assert backend.runs == []


@pytest.mark.parametrize("action,method", [("suspend", "Suspend"), ("hibernate", "Hibernate")])
def test_sleep_actions_use_interactive_logind_request(backend, action, method):
    backend.session_capabilities = (True,) * 7
    result = session.request(action, backend)
    call = backend.calls[-1]
    assert call["bus"] == "system"
    assert call["destination"] == session.LOGIN_NAME
    assert call["path"] == session.LOGIN_PATH
    assert call["interface"] == session.LOGIN_INTERFACE
    assert call["method"] == method
    assert call["signature"] == "(b)"
    assert call["arguments"] == (True,)
    assert result["status"] == "requested"


def test_unsupported_action_does_not_dispatch_anything(backend):
    assert session.request("IgnoreInhibitors", backend)["status"] == "unsupported"
    assert backend.calls == []
    assert backend.runs == []


def test_unavailable_action_does_not_dispatch_action(backend):
    assert session.request("hibernate", backend)["status"] == "unavailable"
    assert all(call["method"] in ("GetCapabilities", "NameHasOwner") for call in backend.calls)


@pytest.mark.parametrize("error", [
    TimeoutError("timed out"), RuntimeError("Operation was cancelled"),
    subprocess.TimeoutExpired("dm-tool", 3),
])
def test_timeout_does_not_claim_action_failed_or_completed(backend, error):
    backend.errors["Shutdown"] = error
    result = session.request("shutdown", backend)
    assert result["status"] == "unknown"
    assert "may still be pending" in result["message"]


def test_desktop_refusal_is_reported_as_error(backend):
    backend.errors["Logout"] = RuntimeError("Access denied")
    result = session.request("logout", backend)
    assert result["status"] == "error"
    assert "Access denied" in result["message"]


def test_missing_gio_is_handled_without_crashing(monkeypatch):
    def missing():
        raise ImportError("gi unavailable")

    monkeypatch.setattr(session, "GioSessionBackend", missing)
    assert not any(session.capabilities().values())
    assert session.request("logout")["status"] == "error"


def test_expired_overall_deadline_stops_capability_requests(backend, monkeypatch):
    moments = iter((0, 4, 5))
    monkeypatch.setattr(session.time, "monotonic", lambda: next(moments))
    assert not any(session.capabilities(backend).values())
    assert backend.calls == []
