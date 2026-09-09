"""The tool registry.

Each tool is a Python function with a JSON schema. The schema goes to the
model. The function does the work.

Control flow for a risky action:

1. The tool function returns a `Confirm` object instead of text.
2. The agent loop stops the task and sets the status to `awaiting-confirmation`.
3. The user clicks Allow or Deny.
4. The agent loop calls the same tool again with `approved=True` or tells the
   model that the user said no.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import Callable

from peppermint import config


@dataclass
class Confirm:
    """A tool returns this when the action needs approval from the user."""

    description: str
    reason: str = ""


@dataclass
class Ask:
    """A tool returns this when Peppermint must ask the user a question."""

    question: str


@dataclass
class Context:
    """State the tool functions may need."""

    task_id: int
    db: object = None
    approved: bool = False
    require_approval: bool = False


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable
    wants_context: bool = False

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


REGISTRY: dict[str, Tool] = {}

# These only ask, track this conversation, or search bundled reference text.
# They cannot inspect the user's computer or execute a proposed remedy.
INTERNAL_TOOLS = frozenset({"ask_user", "set_plan", "linux_reference", "request_retest"})


def tool(name: str, description: str, parameters: dict):
    """Register a function as a tool."""

    def decorator(func: Callable) -> Callable:
        wants_context = "ctx" in inspect.signature(func).parameters
        REGISTRY[name] = Tool(name, description, parameters, func, wants_context)
        return func

    return decorator


def schemas() -> list[dict]:
    return [t.schema() for t in REGISTRY.values()]


def tool_names() -> list[str]:
    return list(REGISTRY)


def truncate(text: str, limit: int | None = None) -> str:
    limit = limit or config.MAX_TOOL_OUTPUT
    if len(text) <= limit:
        return text
    cut = len(text) - limit
    return text[:limit] + f"\n... [{cut} more characters removed]"


class ToolError(Exception):
    """The tool could not run. The message goes back to the model."""


def call(name: str, args: dict, ctx: Context):
    """Run one tool. Returns a string, a Confirm, or an Ask."""
    entry = REGISTRY.get(name)
    if entry is None:
        raise ToolError(
            f"There is no tool named `{name}`. Use one of: {', '.join(REGISTRY)}."
        )

    if "ctx" in args:
        raise ToolError("Tool arguments cannot supply execution permissions.")

    signature = inspect.signature(entry.func)
    accepted = set(signature.parameters)
    unknown = [k for k in args if k not in accepted and k != "ctx"]
    if unknown:
        allowed = sorted(k for k in accepted if k != "ctx")
        raise ToolError(
            f"`{name}` got unknown arguments {unknown}. It accepts: {allowed}."
        )

    required = [
        p.name
        for p in signature.parameters.values()
        if p.default is inspect.Parameter.empty and p.name != "ctx"
    ]
    missing = [r for r in required if r not in args]
    if missing:
        raise ToolError(f"`{name}` needs these arguments: {missing}.")

    if ctx.require_approval and not ctx.approved and name not in INTERNAL_TOOLS:
        return Confirm(
            description=f"{name}\n{json.dumps(args, indent=2, ensure_ascii=False)}",
            reason="Every computer action requires your permission. Allow runs this exact action once.",
        )

    kwargs = dict(args)
    if entry.wants_context:
        kwargs["ctx"] = ctx

    try:
        return entry.func(**kwargs)
    except ToolError:
        raise
    except TypeError as exc:
        raise ToolError(f"`{name}` rejected the arguments: {exc}") from exc
    except Exception as exc:  # a tool must never kill the daemon
        raise ToolError(f"`{name}` failed: {type(exc).__name__}: {exc}") from exc
