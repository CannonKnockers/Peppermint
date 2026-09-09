"""User plugin loading and CLI management surface."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest
from gi.repository import GLib

from peppermint import cli
from peppermint.common import dbus_api
from peppermint.daemon import tools
from peppermint.daemon.plugins import PluginManager
from peppermint.daemon.tools.registry import Confirm, Context, ToolError


def _write_plugin(directory: Path, name: str, body: str) -> None:
    (directory / f"{name}.py").write_text(dedent(body))


def test_plugin_manager_loads_tools_and_context_methods(tmp_path):
    plugin_dir = tmp_path / "tools"
    plugin_dir.mkdir()
    state_path = tmp_path / "state.json"

    _write_plugin(plugin_dir, "sample", """
        import peppermint

        @peppermint.tool(name="plugin_ping", description="return the task id", parameters={"type": "object", "properties": {}})
        def plugin_ping(ctx=None):
            approval = ctx.ask_approval("Allow the hello plugin action.")
            if approval is not None:
                return approval
            return f"ping-{ctx.task_id}"

        @peppermint.tool(name="plugin_echo", description="run a safe command", parameters={"type": "object", "properties": {}}, requires_approval=False)
        def plugin_echo(ctx=None):
            return ctx.run_command("printf 'ok'", purpose="plugin test")
    """)

    manager = PluginManager(plugin_dir=plugin_dir, state_path=state_path)
    manager.load_plugins()
    try:
        assert "plugin_ping" in tools.tool_names()
        assert tools.call("plugin_ping", {}, Context(7, require_approval=False)) == "ping-7"
        assert isinstance(tools.call("plugin_ping", {}, Context(7, require_approval=True)), Confirm)
        assert tools.call("plugin_echo", {}, Context(8, require_approval=False)).strip() == "ok"
    finally:
        manager.disable("sample", notify=False)


def test_plugin_crash_disables_plugin_and_remembers_state(tmp_path):
    plugin_dir = tmp_path / "tools"
    plugin_dir.mkdir()
    state_path = tmp_path / "state.json"

    _write_plugin(plugin_dir, "broken", """
        import peppermint

        @peppermint.tool(name="plugin_boom", description="break every call", parameters={"type": "object", "properties": {}}, requires_approval=False)
        def plugin_boom(ctx=None):
            raise RuntimeError("plugin failure")
    """)

    manager = PluginManager(plugin_dir=plugin_dir, state_path=state_path)
    manager.load_plugins()

    with pytest.raises(ToolError):
        tools.call("plugin_boom", {}, Context(1, require_approval=False))

    with pytest.raises(ToolError):
        tools.call("plugin_boom", {}, Context(2, require_approval=False))
    assert "plugin_boom" not in tools.tool_names()

    recovered = PluginManager(plugin_dir=plugin_dir, state_path=state_path)
    assert recovered.state.disabled == {"broken"}


def test_cli_plugin_commands_call_expected_bus_methods(monkeypatch, capsys):
    calls = []
    plugins_payload = json.dumps([{"name": "sample", "enabled": True, "tools": ["plugin_ping"]}])

    def fake_call(*args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "ListPlugins":
            return GLib.Variant("(s)", (plugins_payload,))
        return None

    monkeypatch.setattr(dbus_api, "call_daemon", fake_call)

    assert cli.main(["plugin", "list"]) == 0
    assert calls[0][0][0] == "ListPlugins"
    assert "sample: enabled (plugin_ping)" in capsys.readouterr().out

    assert cli.main(["plugin", "enable", "sample"]) == 0
    assert calls[1][0][0] == "EnablePlugin"
    assert calls[1][0][1].unpack() == ("sample",)

    assert cli.main(["plugin", "disable", "sample"]) == 0
    assert calls[2][0][0] == "DisablePlugin"
    assert calls[2][0][1].unpack() == ("sample",)


def test_failed_enable_remains_disabled_and_reports_error(tmp_path):
    _write_plugin(tmp_path, 'broken', 'raise RuntimeError("load failed")')
    manager = PluginManager(plugin_dir=tmp_path, state_path=tmp_path / 'state.json')
    with pytest.raises(RuntimeError, match='load failed'):
        manager.enable('broken')
    row = manager.list()[0]
    assert not row['enabled'] and not row['loaded']
    assert row['error'] == 'RuntimeError: load failed'
    recovered = PluginManager(plugin_dir=tmp_path, state_path=tmp_path / 'state.json')
    assert recovered.list()[0]['error'] == row['error']
    _write_plugin(tmp_path, 'broken', '# repaired plugin\n')
    recovered.enable('broken')
    assert recovered.list()[0]['error'] == ''
    assert recovered.list()[0]['loaded']
    recovered.disable('broken', notify=False)


def test_runtime_crash_exposes_error(tmp_path):
    _write_plugin(tmp_path, 'crashy', '''
        import peppermint
        @peppermint.tool(name="audit_crash_tool", description="test", requires_approval=False)
        def audit_crash_tool(ctx=None):
            raise RuntimeError("test crash details")
    ''')
    manager = PluginManager(plugin_dir=tmp_path, state_path=tmp_path / 'state.json')
    manager.load_plugins()
    with pytest.raises(ToolError):
        tools.call('audit_crash_tool', {}, Context(1, require_approval=False))
    row = manager.list()[0]
    assert not row['enabled'] and not row['loaded']
    assert 'test crash details' in row['error']
