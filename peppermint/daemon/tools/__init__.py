"""Import every tool module, so the registry fills up."""

from peppermint.daemon.tools import files, interaction, packages, scheduling, settings, shell, web  # noqa: F401
from peppermint.daemon.tools.registry import (  # noqa: F401
    Ask,
    Confirm,
    Context,
    Tool,
    ToolError,
    call,
    schemas,
    tool_names,
    truncate,
)

__all__ = [
    "Ask", "Confirm", "Context", "Tool", "ToolError",
    "call", "schemas", "tool_names", "truncate",
]
