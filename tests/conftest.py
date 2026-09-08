"""Shared fixtures."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def home_tmp():
    """A temporary folder inside the home directory.

    The safety rules treat a path outside the home directory as risky, and a
    hidden folder as risky too. A test of normal file work must therefore run
    in a plain, visible folder inside the home directory.
    """
    path = Path(__file__).resolve().parents[1] / f"peppermint-test-{uuid.uuid4().hex[:8]}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
