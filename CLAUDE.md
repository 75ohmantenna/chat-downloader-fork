# CLAUDE.md

Follow [`AGENTS.md`](AGENTS.md) for project structure, commands, testing,
style, and commit conventions. When working in a provider package, also follow
its scoped `AGENTS.md`. Use the
[documentation source map](docs/development-workflow-guide.md#documentation-ownership)
to check prose against the implementation.

## Python tooling

This section overrides the global `~/.claude/CLAUDE.md` preferred commands.
Use the project's `uv` environment for development tools; do not invoke a
system- or pipx-installed `ruff`, `mypy`, or `pytest`. Run `uv sync` if the
environment is absent, then use the commands in `AGENTS.md` or the `Makefile`.
