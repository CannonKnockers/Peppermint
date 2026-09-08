# Contributing to Peppermint

Peppermint is a local-first desktop helper for Linux Mint. Contributions should
preserve that local-first behavior, make computer actions explicit, and keep
reversible changes undoable.

## Development setup

Peppermint requires Python 3.10 or newer. On Linux Mint, install the desktop
libraries listed in the README, then create an editable environment:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -e ".[dev]"
```

The Ollama service is only required for end-to-end use. Unit and integration
tests should not require a running model.

## Before opening a pull request

Run the test suite:

```bash
.venv/bin/python -m pytest tests -q
```

Keep changes focused, update documentation for user-visible behavior, and
include the safety and approval implications in the pull request description.
Never include credentials, database files, logs, or machine-specific paths.
