"""Plugin discovery and dispatch integration."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from peppermint.daemon.tools import registry
from peppermint.daemon.tools.registry import Context

log = logging.getLogger("peppermint.plugins")

ACTIVE_MANAGER: "PluginManager | None" = None


class PluginContext(Context):
    """Tool context exposed to plugin implementations."""


def plugin_context(ctx: Context | None) -> PluginContext | None:
    """Return a `PluginContext` for plugin execution."""

    if ctx is None:
        return None
    if isinstance(ctx, PluginContext):
        return ctx
    return PluginContext(task_id=ctx.task_id, db=ctx.db, approved=ctx.approved,
                         require_approval=ctx.require_approval)


def handle_plugin_failure(plugin_name: str, tool_name: str, exc: Exception) -> None:
    """Disable a crashing plugin and report the issue."""

    if ACTIVE_MANAGER is not None:
        try:
            ACTIVE_MANAGER.state.errors[plugin_name] = f"{tool_name}: {type(exc).__name__}: {exc}"
            ACTIVE_MANAGER.disable(plugin_name)
        except Exception:
            ACTIVE_MANAGER.disable(plugin_name, notify=False)
    else:
        for name, tool in list(registry.REGISTRY.items()):
            if tool.plugin == plugin_name:
                registry.REGISTRY.pop(name, None)

    from peppermint.daemon import notifier

    notifier.send(
        "Peppermint plugin failed",
        f"Disabling plugin '{plugin_name}'. Tool '{tool_name}' crashed with "
        f"{type(exc).__name__}: {exc}",
    )
    log.warning("Plugin %s failed in tool %s", plugin_name, tool_name, exc_info=True)


def _extract_plugin_name(module_name: str, declared_name: str) -> str:
    if declared_name:
        return declared_name
    if module_name.startswith("_peppermint_plugin_"):
        stem = module_name[len("_peppermint_plugin_"):]
        stem = re.sub(r"_[0-9a-f]{8}$", "", stem)
        return stem or module_name
    return module_name.split(".")[-1]


def plugin_tool(
    name: str,
    description: str,
    parameters: dict | None = None,
    requires_approval: bool = True,
):
    """Decorator used by user plugin modules."""

    def decorator(func):
        module_name = getattr(func, "__module__", "")
        module = sys.modules.get(module_name)
        declared = ""
        if module is not None:
            declared = getattr(module, "_PEPPERMINT_PLUGIN_NAME__", "") or getattr(module, "PEPPERMINT_PLUGIN_NAME", "")
        plugin_name = _extract_plugin_name(module_name, declared)
        spec = parameters or {"type": "object", "properties": {}}
        return registry.tool(name, description, spec, requires_approval=requires_approval, plugin=plugin_name)(func)

    return decorator


@dataclass
class PluginState:
    disabled: set[str]
    errors: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "PluginState":
        if not path.exists():
            return cls(set())
        try:
            payload = json.loads(path.read_text())
            return cls(set(payload.get("disabled", [])), dict(payload.get("errors", {})))
        except Exception:
            log.warning("Ignoring corrupted plugin state at %s", path)
            return cls(set())

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"disabled": sorted(self.disabled), "errors": self.errors}, indent=2) + "\n")


class PluginManager:
    """Discover plugins and keep tool registrations synchronized."""

    def __init__(self, plugin_dir: Path | None = None, state_path: Path | None = None):
        base_dir = Path.home() / ".config" / "peppermint"
        self.plugin_dir = plugin_dir.expanduser() if plugin_dir else base_dir / "tools"
        self.state_path = state_path.expanduser() if state_path else self.plugin_dir / "plugins.json"
        self.state = PluginState.load(self.state_path)
        self.loaded: set[str] = set()

    @staticmethod
    def _module_name(name: str) -> str:
        safe = re.sub(r"[^a-z0-9]+", "_", name.lower())
        hashed = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        return f"_peppermint_plugin_{safe}_{hashed}"

    def _discover(self) -> list[Path]:
        if not self.plugin_dir.exists():
            return []
        return sorted(path for path in self.plugin_dir.glob("*.py") if path.is_file() and path.name != "__init__.py")

    def _plugin_tools(self, name: str) -> list[str]:
        return [tool_name for tool_name, entry in registry.REGISTRY.items() if entry.plugin == name]

    def _clear_plugin_tools(self, name: str) -> None:
        for tool_name in self._plugin_tools(name):
            registry.REGISTRY.pop(tool_name, None)

    def _load_plugin_file(self, path: Path, name: str) -> None:
        module_name = self._module_name(name)
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load plugin file {path}")

        module = importlib.util.module_from_spec(spec)
        module.__dict__["_PEPPERMINT_PLUGIN_NAME__"] = name
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            pass

    def list(self) -> list[dict[str, Any]]:
        available = sorted(path.stem for path in self._discover())
        return [
            {
                "name": name,
                "enabled": name not in self.state.disabled,
                "path": str((self.plugin_dir / f"{name}.py").resolve()),
                "tools": sorted(self._plugin_tools(name)),
                "loaded": name in self.loaded,
                "error": self.state.errors.get(name, ""),
            }
            for name in available
        ]

    def load_plugins(self) -> None:
        global ACTIVE_MANAGER
        ACTIVE_MANAGER = self

        for path in self._discover():
            name = path.stem
            if name in self.state.disabled:
                self._clear_plugin_tools(name)
                self.loaded.discard(name)
                continue

            if name in self.loaded:
                continue

            try:
                self._load_plugin_file(path, name)
                self.loaded.add(name)
            except Exception as exc:
                # Keep a bad plugin out of service and continue with others.
                self.state.errors[name] = f"{type(exc).__name__}: {exc}"
                self.disable(name, notify=False)
                if name not in self.state.disabled:
                    self.state.disabled.add(name)
                log.warning("Plugin %s was disabled after a load failure: %s", name, exc)

    def enable(self, name: str) -> None:
        available = {path.stem for path in self._discover()}
        if name not in available:
            raise ValueError(f"Unknown plugin: {name}")

        if name not in self.loaded:
            path = self.plugin_dir / f"{name}.py"
            try:
                self._load_plugin_file(path, name)
                self.loaded.add(name)
            except Exception as exc:
                self.state.errors[name] = f"{type(exc).__name__}: {exc}"
                self.disable(name, notify=False)
                raise
        self.state.disabled.discard(name)
        self.state.errors.pop(name, None)
        self.state.save(self.state_path)

    def disable(self, name: str, notify: bool = True) -> None:
        available = {path.stem for path in self._discover()}
        if name not in available:
            if notify:
                raise ValueError(f"Unknown plugin: {name}")
            return

        self.state.disabled.add(name)
        self.state.save(self.state_path)
        self.loaded.discard(name)
        self._clear_plugin_tools(name)

    def list_for_dbus(self) -> str:
        return json.dumps(self.list())

    def list_names(self) -> list[str]:
        return [row["name"] for row in self.list()]


# Keep the top-level public name for plugins: `@peppermint.tool(...)`.
def tool(
    name: str,
    description: str,
    parameters: dict | None = None,
    requires_approval: bool = True,
):
    return plugin_tool(name, description, parameters, requires_approval=requires_approval)
