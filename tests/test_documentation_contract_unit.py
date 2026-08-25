# SPDX-License-Identifier: MIT

"""Contracts for local links and the hand-written Python API reference."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import MISSING, Field, fields
from pathlib import Path
from urllib.parse import unquote

from chat_downloader import __all__ as public_exports
from chat_downloader.cli import _build_arg_parser
from chat_downloader.models import ChatRequest, DownloaderConfig, RunConfig, SiteDefault
from chat_downloader.output.continuous_write import SUPPORTED_OUTPUT_FORMATS
from chat_downloader.runtime import RunResult
from chat_downloader.sites.kick.constants import MESSAGE_GROUPS as KICK_MESSAGE_GROUPS

ROOT = Path(__file__).resolve().parents[1]
API_REFERENCE = ROOT / "docs" / "python-api-reference.md"
CLI_REFERENCE = ROOT / "docs" / "cli-usage.md"
KICK_REFERENCE = ROOT / "docs" / "kick-integration-guide.md"
_MARKDOWN_LINK = re.compile(r"(?<!!)\[[^]]*]\(([^)]+)\)")
_FIELD_ROW = re.compile(
    r"^\| `(?P<name>[a-z_]+)` \| (?P<default>[^|]+?) \|",
    re.MULTILINE,
)


def _project_documents() -> list[Path]:
    """Return human-authored Markdown documents owned by this repository."""
    return sorted(
        {
            *ROOT.glob("*.md"),
            *(ROOT / "docs").rglob("*.md"),
            *(ROOT / "src").rglob("AGENTS.md"),
        }
    )


def _project_text_files() -> list[Path]:
    """Return maintained text files that can carry repository references."""
    suffixes = {".json", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"}
    files = {
        path
        for directory in (
            ROOT / "docs",
            ROOT / "scripts",
            ROOT / "src",
            ROOT / "tests",
        )
        for path in directory.rglob("*")
        if path.is_file() and path.suffix in suffixes
    }
    files.update(ROOT.glob("*.md"))
    files.add(ROOT / "pyproject.toml")
    return sorted(files)


def _section(text: str, heading: str) -> str:
    """Return a level-three Markdown section through the next peer."""
    match = re.search(
        rf"^{re.escape(heading)}\s*$.*?(?=^###\s|^##\s|\Z)",
        text,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert match is not None, f"missing documentation section {heading!r}"
    return match.group(0)


def _field_default(field: Field[object]) -> object:
    """Return a dataclass field's effective default."""
    if field.default is not MISSING:
        return field.default
    if field.default_factory is not MISSING:
        return field.default_factory()
    raise AssertionError(f"public field {field.name!r} has no default")


def _documented_default(value: object) -> str:
    """Render a public dataclass default as used by the API table."""
    if isinstance(value, SiteDefault):
        return "site default"
    if isinstance(value, str):
        return f'`"{value}"`'
    return f"`{value}`"


def _documented_fields(heading: str) -> list[tuple[str, str]]:
    """Return field/default rows from one typed-configuration section."""
    section = _section(API_REFERENCE.read_text(encoding="utf-8"), heading)
    return [
        (match.group("name"), match.group("default").strip())
        for match in _FIELD_ROW.finditer(section)
    ]


def test_local_markdown_links_resolve() -> None:
    """Reject broken relative links in project-owned Markdown."""
    broken: list[str] = []

    for document in _project_documents():
        text = document.read_text(encoding="utf-8")
        for raw_target in _MARKDOWN_LINK.findall(text):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            relative = unquote(target.split("#", 1)[0])
            if relative and not (document.parent / relative).exists():
                doc_name = document.relative_to(ROOT).as_posix()
                broken.append(f"{doc_name}: {target}")

    assert not broken, "broken local Markdown links:\n" + "\n".join(broken)


def test_project_does_not_reference_upstream_issues() -> None:
    """Keep fork-owned surfaces independent from upstream issue trackers."""
    forbidden = "github.com/" + "xenova/chat-downloader/" + "issues/"
    references = [
        path.relative_to(ROOT).as_posix()
        for path in _project_text_files()
        if forbidden in path.read_text(encoding="utf-8")
    ]

    assert not references, "upstream issue references found:\n" + "\n".join(references)


def test_fork_history_does_not_reference_issue_numbers() -> None:
    """Reject commit messages that GitHub could link to upstream issues."""
    script = ROOT / "scripts" / "check_issue_references.py"
    result = subprocess.run(  # noqa: S603 - fixed local interpreter and script
        [sys.executable, str(script)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_python_api_reference_lists_exact_top_level_exports() -> None:
    """Keep the documented import block aligned with package ``__all__``."""
    text = API_REFERENCE.read_text(encoding="utf-8")
    match = re.search(
        r"from chat_downloader import \(\n(?P<body>.*?)\n\)",
        text,
        flags=re.DOTALL,
    )
    assert match is not None, "Python API reference is missing its import block"
    documented = {
        line.strip().removesuffix(",")
        for line in match.group("body").splitlines()
        if line.strip()
    }
    assert documented == set(public_exports)


def test_python_api_reference_documents_run_result_fields() -> None:
    """Keep the observable ``run()`` result shape documented."""
    section = _section(API_REFERENCE.read_text(encoding="utf-8"), "### `run`")
    documented = [match.group("name") for match in _FIELD_ROW.finditer(section)]

    assert documented == [field.name for field in fields(RunResult)]


def test_python_api_reference_documents_exact_typed_fields_and_defaults() -> None:
    """Keep typed tables in dataclass order with code-derived defaults."""
    for heading, dataclass_type in (
        ("### `DownloaderConfig`", DownloaderConfig),
        ("### `ChatRequest`", ChatRequest),
        ("### `RunConfig`", RunConfig),
    ):
        expected = [
            (field.name, _documented_default(_field_default(field)))
            for field in fields(dataclass_type)
        ]
        assert _documented_fields(heading) == expected


def test_cli_reference_mentions_only_real_cli_flags() -> None:
    """Reject stale option names in the user-facing CLI guide."""
    text = CLI_REFERENCE.read_text(encoding="utf-8")
    documented = set(re.findall(r"`(--[a-z][a-z0-9_-]*)", text))
    parser = _build_arg_parser()
    actual = {option for action in parser._actions for option in action.option_strings}

    assert documented <= actual


def test_cli_reference_lists_exact_output_formats() -> None:
    """Keep the output-format table aligned with writer dispatch."""
    section = _section(CLI_REFERENCE.read_text(encoding="utf-8"), "## Output Formats")
    documented = set(re.findall(r"^\| `([a-z0-9]+)`\s*\|", section, re.MULTILINE))

    assert documented == set(SUPPORTED_OUTPUT_FORMATS)


def test_kick_reference_lists_exact_message_groups() -> None:
    """Keep the Kick message-group table aligned with provider constants."""
    section = _section(
        KICK_REFERENCE.read_text(encoding="utf-8"),
        "## Message Groups and Types",
    )
    rows = re.findall(r"^\| `([^`]+)` \| ([^|]+) \|", section, re.MULTILINE)
    documented = {
        group: re.findall(r"`([^`]+)`", message_types) for group, message_types in rows
    }

    assert documented == KICK_MESSAGE_GROUPS
