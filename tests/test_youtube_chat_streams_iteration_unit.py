# SPDX-License-Identifier: MIT

"""Isolated unit tests for continuation pure helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from chat_downloader.errors import (
    ChatDownloaderError,
    NoChatReplay,
    NoContinuation,
)
from chat_downloader.sites.youtube.continuation import (
    _ContinuationLoop,
    _raise_if_api_error,
    _resolve_poll_delay_ms,
    _select_initial_continuation,
)


def _attempt_profile_fallback(downloader: object) -> bool:
    """Drive the profile-fallback method on a loop bound to *downloader*."""
    loop = _ContinuationLoop(cast("Any", downloader), {}, {}, cast("Any", None))
    return loop._attempt_profile_fallback()


# ── _raise_if_api_error ──────────────────────────────────────────────────────


def test_raise_if_api_error_no_op_when_no_error_key() -> None:
    _raise_if_api_error({"continuationContents": {}})


def test_raise_if_api_error_does_not_infer_availability_from_generic_400() -> None:
    with pytest.raises(
        ChatDownloaderError, match="rejected the chat continuation"
    ) as exc:
        _raise_if_api_error({"error": {"code": 400, "message": "bad request"}})
    assert not isinstance(exc.value, NoChatReplay)


@pytest.mark.parametrize("code", [403, 500, "403", ""])
def test_raise_if_api_error_raises_chat_downloader_error_for_non_400(
    code: object,
) -> None:
    with pytest.raises(ChatDownloaderError):
        _raise_if_api_error({"error": {"code": code, "message": "error"}})


@pytest.mark.parametrize(
    ("error", "match"),
    [(None, "Unknown error"), ("some plain string error", None)],
)
def test_raise_if_api_error_non_dict_value(error, match) -> None:
    with pytest.raises(ChatDownloaderError, match=match):
        _raise_if_api_error({"error": error})


# ── _select_initial_continuation ─────────────────────────────────────────────


@pytest.mark.parametrize("chat_type", ["top", "live"])
@pytest.mark.parametrize(
    ("is_replay", "has_replay_label"),
    [(True, True), (True, False), (False, False)],
    ids=["replay-preferred", "replay-fallback", "live"],
)
def test_select_initial_continuation_returns_correct_label_and_token(
    chat_type,
    is_replay,
    has_replay_label,
) -> None:
    live_label = f"{chat_type.title()} chat"
    info = {live_label: "live-token"}
    if has_replay_label:
        info[f"{live_label} replay"] = "replay-token"
    expected_label = f"{live_label} replay" if has_replay_label else live_label
    label, token = _select_initial_continuation(
        info,
        chat_type=chat_type,
        is_replay=is_replay,
    )
    assert label == expected_label
    assert token == info[expected_label]


@pytest.mark.parametrize(
    ("info", "chat_type", "is_replay", "match"),
    [
        ({"Unrelated": "tok"}, "top", False, None),
        ({}, "live", True, None),
        ({"Live chat replay": "tok"}, "top", False, "Live chat replay"),
    ],
)
def test_select_initial_continuation_raises_when_label_absent(
    info,
    chat_type,
    is_replay,
    match,
) -> None:
    with pytest.raises(NoContinuation, match=match):
        _select_initial_continuation(info, chat_type=chat_type, is_replay=is_replay)


# ── _resolve_poll_delay_ms ───────────────────────────────────────────────────


_FALLBACK = 5000
_MIN = 500
_MAX = 8000


@pytest.mark.parametrize(
    ("timeout_ms", "expected"),
    [
        (None, _FALLBACK),
        (True, _FALLBACK),  # booleans are excluded
        (False, _FALLBACK),
        (-1, _FALLBACK),  # negative → fallback
        (100, _MIN),  # below min → clamped to min
        (500, _MIN),  # exactly min
        (1000, 1000),  # in range
        (8000, _MAX),  # exactly max
        (10000, _MAX),  # above max → clamped to max
        ("3000", 3000),  # valid numeric string
        ("abc", _FALLBACK),  # non-numeric string
    ],
)
def test_resolve_poll_delay_ms(timeout_ms: object, expected: int) -> None:
    assert _resolve_poll_delay_ms(timeout_ms) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("replay_poll_interval", "expected"),
    [
        (None, 5000),
        (0.5, 500),
        (0.75, 750),
        (1.0, 1000),
        (8.0, 8000),
    ],
)
def test_resolve_poll_delay_uses_explicit_replay_override(
    replay_poll_interval: float,
    expected: int,
) -> None:
    assert (
        _resolve_poll_delay_ms(
            5000,
            replay_poll_interval=replay_poll_interval,
        )
        == expected
    )


# ── _attempt_profile_fallback ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("enabled", "next_profile", "applied", "expected"),
    [
        (False, None, False, False),
        (True, None, False, False),
        (True, "youtube_android", True, True),
        (True, "youtube_android", False, False),
    ],
    ids=["disabled", "exhausted", "applied", "apply-failed"],
)
def test_attempt_profile_fallback(
    monkeypatch, enabled, next_profile, applied, expected
):
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.get_next_request_profile",
        lambda profile, site: next_profile,
    )
    downloader = SimpleNamespace(
        _auto_profile_fallback=enabled,
        _request_profile="default" if enabled else None,
        apply_request_profile=lambda profile: applied,
    )
    assert _attempt_profile_fallback(downloader) is expected
