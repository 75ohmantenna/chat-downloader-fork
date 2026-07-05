# SPDX-License-Identifier: MIT

"""Focused contract tests for generated CLI help."""

from __future__ import annotations

from chat_downloader.cli import _build_arg_parser


def test_generated_help_preserves_key_argument_groups() -> None:
    help_text = _build_arg_parser().format_help()

    for heading in (
        "Mandatory Arguments:",
        "General Arguments:",
        "Timing Arguments:",
        "Message Type Arguments:",
        "Retry Arguments:",
        "Termination Arguments:",
        "Format Arguments:",
        "[Site Specific] YouTube Arguments:",
        "[Site Specific] Twitch Arguments:",
        "Output Arguments:",
        "Debugging/Testing Arguments:",
        "Initialisation Arguments:",
    ):
        assert heading in help_text


def test_generated_help_lists_only_supported_output_formats() -> None:
    help_text = _build_arg_parser().format_help()
    normalized_help = " ".join(help_text.split())

    assert ".jsonl/.txt" in normalized_help
    assert "Other extensions are not supported" in normalized_help
    assert "use .jsonl for structured output" in normalized_help
