# CLAUDE.md

Claude-specific agent instructions are intentionally kept in
[`AGENTS.md`](AGENTS.md) so agent guidance has one source of truth.

For project structure, commands, style, testing, commit format, and repository
notes, follow [`AGENTS.md`](AGENTS.md).

## Python tooling

This section overrides the global `~/.claude/CLAUDE.md` preferred commands.
Always invoke tools through the project `.venv`; never use system- or
pipx-installed `ruff`, `mypy`, or `pytest`.

Bootstrap (once, if `.venv` is absent):
```
python3 -m venv .venv
.venv/bin/pip3 install -e ".[dev]"
```

Preferred commands:
```
.venv/bin/python3 -m pytest -q -p no:rerunfailures -m "not network"
.venv/bin/python3 -m ruff check chat_downloader tests
.venv/bin/python3 -m ruff format --check chat_downloader tests
.venv/bin/python3 -m ruff format chat_downloader tests
.venv/bin/python3 -m mypy .
```
