# SPDX-License-Identifier: MIT

"""Composition tests for the _ContinuationLoop object.

These drive the real assembly of the continuation loop (setup + parse + advance
+ end-message injection) through the loop object's public entry points, faking
only the HTTP boundary (`_get_continuation_info`). The per-behavior branches
(profile fallback, no-progress guard, live-timing, replay) are covered against
the same real assembly in test_youtube_runtime_unit.py and
test_youtube_continuation_guards_unit.py; this file pins the object/factory
contract itself.
"""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from typing import Any, cast

import pytest

from chat_downloader.models import ChatRequest
from chat_downloader.sites.youtube.continuation import (
    _ContinuationLoop,
    _get_chat_messages,
)


class _DummyDownloader:
    def __init__(self) -> None:
        self.session = SimpleNamespace(headers={})
        self._session_post = object()
        self._request_profile = "youtube_web"
        self._auto_profile_fallback = True

    def update_session_headers(self, headers: dict[str, str]) -> None:
        self.session.headers.update(headers)

    def apply_request_profile(self, profile_name: str) -> bool:
        return True


def _make_loop(message_groups: list[str] | None = None) -> _ContinuationLoop:
    return _ContinuationLoop(
        cast("Any", _DummyDownloader()),
        {"continuation_info": {"Live chat": "token"}, "status": "live"},
        {"INNERTUBE_API_KEY": "key"},
        ChatRequest(
            url="https://www.youtube.com/watch?v=abc",
            chat_type="live",
            message_groups=message_groups or ["messages"],
        ),
    )


@pytest.fixture(autouse=True)
def _stub_setup_boundaries(monkeypatch) -> None:
    """Fake only the network/auth boundaries; keep parse/advance real."""
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._generate_headers",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._generate_sapisidhash_header",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._get_innertube_context",
        lambda _ytcfg: {"client": {}},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.polling_sleep",
        lambda _seconds: None,
    )


def test_get_chat_messages_factory_returns_generator() -> None:
    """The factory wires a _ContinuationLoop and returns its run() generator."""
    result = _get_chat_messages(
        cast("Any", _DummyDownloader()),
        {"continuation_info": {"Live chat": "token"}, "status": "live"},
        {"INNERTUBE_API_KEY": "key"},
        ChatRequest(url="https://www.youtube.com/watch?v=abc", chat_type="live"),
    )
    assert isinstance(result, Generator)


def test_run_yields_chat_ended_on_clean_live_end(monkeypatch) -> None:
    """A live payload with no continuations ends cleanly and injects chat_ended.

    Exercises the real parse_continuation_response + _advance_continuation_loop
    path (no continuations → is_end → ended_cleanly) and the real message
    filter (chat_ended belongs to the "engagement" group), only faking the HTTP
    call.
    """
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._get_continuation_info",
        lambda *_a, **_k: {
            "continuationContents": {"liveChatContinuation": {"actions": []}},
        },
    )

    messages = list(_make_loop(message_groups=["engagement"]).run())

    assert messages == [
        {
            "message_type": "chat_ended",
            "action_type": "chat_ended",
            "message": None,
        }
    ]


def test_run_omits_chat_ended_when_group_not_requested(monkeypatch) -> None:
    """Clean end terminates without yielding chat_ended if its group is excluded."""
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._get_continuation_info",
        lambda *_a, **_k: {
            "continuationContents": {"liveChatContinuation": {"actions": []}},
        },
    )

    # Default "messages" group does not include chat_ended (engagement group).
    assert list(_make_loop().run()) == []


def test_run_raises_incomplete_when_live_chat_continuation_missing(
    monkeypatch,
) -> None:
    """A payload lacking liveChatContinuation surfaces IncompleteContinuationError."""
    from chat_downloader.errors import IncompleteContinuationError

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._get_continuation_info",
        lambda *_a, **_k: {"responseContext": {}},
    )

    with pytest.raises(IncompleteContinuationError):
        list(_make_loop().run())
