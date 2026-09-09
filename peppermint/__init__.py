"""Top-level package helpers."""

from __future__ import annotations


def tool(
    name: str,
    description: str,
    parameters: dict | None = None,
    requires_approval: bool = True,
):
    from peppermint.daemon.plugins import tool as plugin_tool

    return plugin_tool(
        name,
        description,
        parameters or {"type": "object", "properties": {}},
        requires_approval=requires_approval,
    )
