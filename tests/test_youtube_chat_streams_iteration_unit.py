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


def test_raise_if_api_error_raises_no_chat_replay_for_400() -> None:
    with pytest.raises(NoChatReplay):
        _raise_if_api_error({"error": {"code": 400, "message": "bad request"}})


@pytest.mark.parametrize("code", [403, 500, "403", ""])
def test_raise_if_api_error_raises_chat_downloader_error_for_non_400(
    code: object,
) -> None:
    with pytest.raises(ChatDownloaderError):
        _raise_if_api_error({"error": {"code": code, "message": "error"}})


def test_raise_if_api_error_non_dict_none_error_value() -> None:
    with pytest.raises(ChatDownloaderError, match="Unknown error"):
        _raise_if_api_error({"error": None})


def test_raise_if_api_error_non_dict_string_error_value() -> None:
    with pytest.raises(ChatDownloaderError):
        _raise_if_api_error({"error": "some plain string error"})


# ── _select_initial_continuation ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("info", "chat_type", "is_replay", "expected_label"),
    [
        (
            {"Top chat replay": "tok1", "Top chat": "tok2"},
            "top",
            True,
            "Top chat replay",
        ),
        (
            {"Top chat": "tok2"},
            "top",
            True,
            "Top chat",
        ),
        (
            {"Top chat": "tok3"},
            "top",
            False,
            "Top chat",
        ),
        (
            {"Live chat replay": "tok4", "Live chat": "tok5"},
            "live",
            True,
            "Live chat replay",
        ),
        (
            {"Live chat": "tok5"},
            "live",
            True,
            "Live chat",
        ),
        (
            {"Live chat": "tok6"},
            "live",
            False,
            "Live chat",
        ),
    ],
)
def test_select_initial_continuation_returns_correct_label_and_token(
    info: dict[str, str],
    chat_type: str,
    is_replay: bool,
    expected_label: str,
) -> None:
    label, token = _select_initial_continuation(
        info, chat_type=chat_type, is_replay=is_replay
    )
    assert label == expected_label
    assert token == info[expected_label]


def test_select_initial_continuation_raises_when_label_absent() -> None:
    with pytest.raises(NoContinuation):
        _select_initial_continuation(
            {"Unrelated": "tok"}, chat_type="top", is_replay=False
        )


def test_select_initial_continuation_raises_with_empty_info() -> None:
    with pytest.raises(NoContinuation):
        _select_initial_continuation({}, chat_type="live", is_replay=True)


def test_select_initial_continuation_error_message_lists_available() -> None:
    with pytest.raises(NoContinuation, match="Live chat replay"):
        _select_initial_continuation(
            {"Live chat replay": "tok"},
            chat_type="top",
            is_replay=False,
        )


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


# ── _attempt_profile_fallback ─────────────────────────────────────────────────


def test_attempt_profile_fallback_returns_false_when_disabled() -> None:
    self_ = SimpleNamespace(_auto_profile_fallback=False, _request_profile=None)
    assert _attempt_profile_fallback(self_) is False


def test_attempt_profile_fallback_returns_false_when_no_next_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.get_next_request_profile",
        lambda profile, site: None,
    )
    self_ = SimpleNamespace(_auto_profile_fallback=True, _request_profile="default")
    assert _attempt_profile_fallback(self_) is False


def test_attempt_profile_fallback_returns_true_when_profile_applied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.get_next_request_profile",
        lambda profile, site: "youtube_android",
    )
    self_ = SimpleNamespace(
        _auto_profile_fallback=True,
        _request_profile="default",
        apply_request_profile=lambda p: True,
    )
    assert _attempt_profile_fallback(self_) is True


def test_attempt_profile_fallback_returns_false_when_apply_profile_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.get_next_request_profile",
        lambda profile, site: "youtube_android",
    )
    self_ = SimpleNamespace(
        _auto_profile_fallback=True,
        _request_profile="default",
        apply_request_profile=lambda p: False,
    )
    assert _attempt_profile_fallback(self_) is False
