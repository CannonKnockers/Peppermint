"""Shared data types for the daemon, the CLI, and the window."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum


class Status(str, Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting-confirmation"
    AWAITING_INPUT = "awaiting-input"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_final(self) -> bool:
        return self in (Status.DONE, Status.FAILED, Status.CANCELLED)

    @property
    def needs_user(self) -> bool:
        return self in (Status.AWAITING_CONFIRMATION, Status.AWAITING_INPUT)

    @property
    def is_active(self) -> bool:
        return self in (Status.QUEUED, Status.PLANNING, Status.RUNNING)


class Risk(str, Enum):
    SAFE = "safe"
    RISKY = "risky"


@dataclass
class Verdict:
    """The result of the safety check for one action."""

    risk: Risk
    reason: str

    @property
    def safe(self) -> bool:
        return self.risk is Risk.SAFE


@dataclass
class Step:
    id: int
    task_id: int
    tool: str
    args: dict
    risk: str
    output: str
    status: str
    ts: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Confirmation:
    id: int
    task_id: int
    step_id: int
    description: str
    resolved: bool
    approved: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Task:
    id: int
    idea: str
    status: str
    parent_task_id: int
    created_at: str
    updated_at: str
    result: str = ""
    error: str = ""
    steps: list[Step] = field(default_factory=list)
    pending: Confirmation | None = None
    question: str = ""
    messages: list[dict] = field(default_factory=list)
    plan: list[dict] = field(default_factory=list)
    retest: dict | None = None

    def to_dict(self) -> dict:
        from peppermint.common.secrets import mask
        d = asdict(self)
        return mask(d, getattr(self, "_display_secrets", set()))

    def to_json(self) -> str:
        return json.dumps(self.to_dict())
