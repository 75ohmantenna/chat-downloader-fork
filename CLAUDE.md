# CLAUDE.md

Claude-specific agent instructions are intentionally kept in
[`AGENTS.md`](AGENTS.md) so agent guidance has one source of truth.

For project structure, commands, style, testing, commit format, and repository
notes, follow [`AGENTS.md`](AGENTS.md).

## Python tooling

This section overrides the global `~/.claude/CLAUDE.md` preferred commands.
Use `uv` for all development tasks; do not use system- or pipx-installed
`ruff`, `mypy`, or `pytest` directly.

Bootstrap (once, if `.venv` is absent):
```
uv sync
```

Preferred commands:
```
uv run pytest -q -p no:rerunfailures -m "not network"
uv run ruff check chat_downloader tests
uv run ruff format --check chat_downloader tests
uv run ruff format chat_downloader tests
uv run mypy .
```
