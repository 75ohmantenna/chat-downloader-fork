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
        "Initialization Arguments:",
    ):
        assert heading in help_text
    normalized_help = " ".join(help_text.split())

    assert ".jsonl/.txt" in normalized_help
    assert "Other extensions are not supported" in normalized_help
    assert "use .jsonl for structured output" in normalized_help
    assert "Use --quiet to suppress stdout" in normalized_help
    assert "Timed YouTube request retries wait out their delay" in normalized_help
    assert "Video IDs to skip during YouTube channel discovery" in normalized_help
    assert "The /live shortcut resolves directly to a video" in normalized_help
    assert "Twitch and Kick channel URLs cannot seek" in normalized_help
    assert "mutually exclusive with --message_groups" in normalized_help
    assert "seconds without an emitted message" in normalized_help
    assert "socks5:// resolves the destination locally" in normalized_help
    assert "socks5h:// resolves it through the proxy" in normalized_help
    assert "Pass false to append" in normalized_help
