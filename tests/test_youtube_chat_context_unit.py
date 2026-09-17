# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import Any, cast

import pytest

from chat_downloader.errors import InvalidParameter, NoContinuation
from chat_downloader.models import ChatRequest
from chat_downloader.request_profiles import REQUEST_PROFILE_INNERTUBE_CONTEXTS
from chat_downloader.sites.filters import TimeRangeFilter
from chat_downloader.sites.youtube.continuation import (
    _build_continuation_urls,
    _build_message_filters,
    _ContinuationLoop,
)

URL = "https://www.youtube.com/watch?v=abc"


def _build_chat_context(downloader, initial_info, ytcfg, params):
    return _ContinuationLoop(
        cast("Any", downloader), initial_info, ytcfg, params
    )._build_context()


def _apply_session_headers(downloader, ytcfg, init_page):
    loop = _ContinuationLoop(cast("Any", downloader), {}, ytcfg, cast("Any", None))
    loop._apply_session_headers(init_page)


def _patch_http(monkeypatch, *, headers=None, innertube=True):
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._generate_headers",
        lambda *_a, **_k: headers or {},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._generate_sapisidhash_header",
        lambda *_a, **_k: None,
    )
    if innertube:
        monkeypatch.setattr(
            "chat_downloader.sites.youtube.continuation._get_innertube_context",
            lambda _ytcfg: {"client": {}},
        )


class _DummySession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}


class _DummyDownloader:
    def __init__(self) -> None:
        self.session = _DummySession()
        self._session_post = object()
        self.invalid_type_checks: list[tuple[object, object]] = []
        self._request_profile = "youtube_web"
        self._auto_profile_fallback = True

    def check_for_invalid_types(self, message_types, valid_types) -> None:
        self.invalid_type_checks.append((message_types, valid_types))

    def update_session_headers(self, headers) -> None:
        self.session.headers.update(headers)

    def replace_session_headers(self, headers, managed_names) -> None:
        for name in managed_names:
            self.session.headers.pop(name, None)
        self.update_session_headers(headers)

    def apply_request_profile(self, profile_name: str) -> bool:
        self._request_profile = profile_name
        return True


def _make_initial_info(status="live"):
    return {
        "continuation_info": {"Top chat": "top-token", "Live chat": "live-token"},
        "status": status,
    }


@pytest.mark.parametrize(
    ("status", "endpoint"),
    [("live", "live_chat"), ("past", "live_chat_replay")],
    ids=["live", "replay"],
)
def test_build_chat_context_url_reflects_replay_status(monkeypatch, status, endpoint):
    """_build_chat_context derives the API URL from ytcfg and replay status."""
    _patch_http(monkeypatch)
    ctx = _build_chat_context(
        _DummyDownloader(),
        _make_initial_info(status=status),
        {"INNERTUBE_API_KEY": "testkey"},
        ChatRequest(url=URL, chat_type="live"),
    )

    assert ctx.is_replay is (status == "past")
    assert endpoint in ctx.continuation_url
    assert ("replay" in ctx.continuation_url) == (status == "past")
    assert "testkey" in ctx.continuation_url


def test_build_chat_context_selects_chat_type_by_label_not_insertion_order(
    monkeypatch,
) -> None:
    """_build_chat_context should not assume submenu insertion order."""
    _patch_http(monkeypatch)

    ctx = _build_chat_context(
        _DummyDownloader(),
        {
            "continuation_info": {
                "Live chat": "live-token",
                "Top chat": "top-token",
            },
            "status": "live",
        },
        {"INNERTUBE_API_KEY": "testkey"},
        ChatRequest(url=URL, chat_type="top"),
    )

    assert ctx.loop_state.continuation == "top-token"


def test_build_chat_context_applies_request_profile_to_innertube_context(
    monkeypatch,
) -> None:
    _patch_http(monkeypatch, innertube=False)

    downloader = _DummyDownloader()
    downloader._request_profile = "youtube_android"
    ctx = _build_chat_context(
        downloader,
        _make_initial_info(status="live"),
        {
            "INNERTUBE_API_KEY": "testkey",
            "INNERTUBE_CONTEXT": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "old",
                },
            },
        },
        ChatRequest(url=URL, chat_type="live"),
    )

    assert ctx.innertube_context["client"]["clientName"] == "ANDROID"
    assert (
        ctx.innertube_context["client"]["androidSdkVersion"]
        == REQUEST_PROFILE_INNERTUBE_CONTEXTS["youtube_android"]["client"][
            "androidSdkVersion"
        ]
    )


@pytest.mark.parametrize(
    ("initial_info", "kwargs", "error", "match"),
    [
        (
            {"continuation_info": {"Top chat": "only-one"}, "status": "live"},
            {"chat_type": "live"},
            NoContinuation,
            None,
        ),
        (
            _make_initial_info(status="live"),
            {"message_groups": ["no-such-group"]},
            InvalidParameter,
            "Invalid groups specified",
        ),
    ],
    ids=["missing-chat-type", "unknown-group"],
)
def test_build_chat_context_rejects_missing_chat_type_or_bad_group(
    monkeypatch, initial_info, kwargs, error, match
) -> None:
    """_build_chat_context raises for an absent chat_type or unknown group."""
    _patch_http(monkeypatch)

    with pytest.raises(error, match=match):
        _build_chat_context(
            _DummyDownloader(),
            initial_info,
            {"INNERTUBE_API_KEY": "key"},
            ChatRequest(url=URL, **kwargs),
        )


def test_build_chat_context_message_types_override_default_groups(
    monkeypatch,
) -> None:
    """Explicit message types exclude resolved site-default groups."""
    _patch_http(monkeypatch)

    ctx = _build_chat_context(
        _DummyDownloader(),
        _make_initial_info(status="live"),
        {"INNERTUBE_API_KEY": "key"},
        ChatRequest(
            url=URL, message_groups=["messages"], message_types=["paid_message"]
        ),
    )

    assert ctx.msg_filter.should_add({"message_type": "paid_message"})
    assert not ctx.msg_filter.should_add({"message_type": "text_message"})


# Continuation URL helper contracts.


@pytest.mark.parametrize(
    ("page", "api"),
    [("live_chat", "get_live_chat"), ("live_chat_replay", "get_live_chat_replay")],
    ids=["live", "replay"],
)
def test_build_continuation_urls(page, api) -> None:
    init_page, url = _build_continuation_urls(
        "TOKEN", "KEY", is_replay="replay" in page
    )
    assert "continuation=TOKEN" in init_page
    assert f"{page}?" in init_page
    assert f"{api}?key=KEY" in url
    assert ("replay" in url) == ("replay" in page)
    assert ("replay" in init_page) == ("replay" in page)


# Message-filter helper contracts.


def _filters(*, request=None, is_replay=False, start_time=None):
    return _build_message_filters(
        ChatRequest(url=URL, **(request or {})),
        is_replay=is_replay,
        start_time=start_time,
        end_time=None,
        offset=None,
    )


@pytest.mark.parametrize(
    "params", [{}, {"message_groups": "messages"}], ids=["no-groups", "non-list-groups"]
)
def test_build_message_filters_live_defaults_to_no_time_or_group_filter(params) -> None:
    msg_filter, time_filter = _filters(request=params)
    assert time_filter is None
    assert msg_filter is not None


def test_build_message_filters_replay_skips_pre_range_actions_across_pages() -> None:
    _msg_filter, time_filter = _filters(is_replay=True, start_time=10.0)
    assert isinstance(time_filter, TimeRangeFilter)
    assert time_filter.check({"time_in_seconds": 9.0}) == "skip"

    time_filter.end_page()

    assert time_filter.check({"time_in_seconds": 9.5}) == "skip"
    assert time_filter.check({"time_in_seconds": 10.0}) == "yield"


def test_build_message_filters_types_override_groups() -> None:
    """Explicit message_types causes message_groups to be ignored."""
    msg_filter, _ = _filters(
        request={"message_groups": ["messages"], "message_types": ["paid_message"]}
    )
    assert msg_filter.should_add({"message_type": "paid_message"})
    assert not msg_filter.should_add({"message_type": "text_message"})


# Session-header helper contracts.


def test_apply_session_headers_replaces_stale_auth_and_preserves_custom(monkeypatch):
    _patch_http(monkeypatch, headers={"x-youtube-client-name": "1"}, innertube=False)
    downloader = _DummyDownloader()
    downloader.session.headers.update(
        {
            "authorization": "stale-auth",
            "x-youtube-identity-token": "stale-identity",
            "x-custom": "preserved",
        }
    )
    _apply_session_headers(downloader, {}, "https://www.youtube.com/init")
    assert "content-type" in downloader.session.headers
    assert "referer" in downloader.session.headers
    assert "authorization" not in downloader.session.headers
    assert "x-youtube-identity-token" not in downloader.session.headers
    assert downloader.session.headers["x-custom"] == "preserved"
