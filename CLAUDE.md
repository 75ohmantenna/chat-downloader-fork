# CLAUDE.md

Follow [`AGENTS.md`](AGENTS.md) for project structure, commands, testing,
style, and commit conventions. The actual checked-in codebase is the single
source of truth for documentation of implemented behavior, including this
file. Verify prose against source code, bundled data, and configuration; tests
check their contracts. Correct prose when it differs from the implementation.

## Python tooling

This section overrides the global `~/.claude/CLAUDE.md` preferred commands.
Use the project's `uv` environment for development tools; do not invoke a
system- or pipx-installed `ruff`, `mypy`, or `pytest`. Run `uv sync` if the
environment is absent, then use the commands in `AGENTS.md` or the `Makefile`.
