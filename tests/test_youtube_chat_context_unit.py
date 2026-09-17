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
from tests.youtube_third_helpers import Downloader, returns, video_info

URL = "https://www.youtube.com/watch?v=abc"


def _loop(owner=None, info=None, config=None, **params):
    return _ContinuationLoop(
        cast("Any", owner or Downloader()),
        video_info() if info is None else info,
        {"INNERTUBE_API_KEY": "testkey", **(config or {})},
        ChatRequest(url=URL, **params),
    )


def _patch_http(monkeypatch, *, headers=None, innertube=True):
    returns(monkeypatch, "continuation._generate_headers", headers or {})
    returns(monkeypatch, "continuation._generate_sapisidhash_header", None)
    if innertube:
        returns(monkeypatch, "continuation._get_innertube_context", {"client": {}})


@pytest.mark.parametrize(
    ("status", "endpoint"), [("live", "live_chat"), ("past", "live_chat_replay")]
)
def test_context_replay_url(monkeypatch, status, endpoint):
    _patch_http(monkeypatch)
    ctx = _loop(info=video_info(status), chat_type="live")._build_context()
    assert ctx.is_replay is (status == "past")
    assert endpoint in ctx.continuation_url
    assert ("replay" in ctx.continuation_url) == (status == "past")
    assert "testkey" in ctx.continuation_url


def test_context_selects_label_not_insertion_order(monkeypatch):
    _patch_http(monkeypatch)
    info = {
        "continuation_info": {"Live chat": "live-token", "Top chat": "top-token"},
        "status": "live",
    }
    assert _loop(
        info=info, chat_type="top"
    )._build_context().loop_state.continuation == ("top-token")


def test_context_applies_profile(monkeypatch):
    _patch_http(monkeypatch, innertube=False)
    owner = Downloader()
    owner._request_profile = "youtube_android"
    config = {
        "INNERTUBE_CONTEXT": {"client": {"clientName": "WEB", "clientVersion": "old"}}
    }
    ctx = _loop(owner, config=config, chat_type="live")._build_context()
    client = ctx.innertube_context["client"]
    assert client["clientName"] == "ANDROID"
    assert (
        client["androidSdkVersion"]
        == REQUEST_PROFILE_INNERTUBE_CONTEXTS["youtube_android"]["client"][
            "androidSdkVersion"
        ]
    )


@pytest.mark.parametrize(
    ("info", "params", "error", "match"),
    [
        (
            {"continuation_info": {"Top chat": "only-one"}, "status": "live"},
            {"chat_type": "live"},
            NoContinuation,
            None,
        ),
        (
            video_info(),
            {"message_groups": ["no-such-group"]},
            InvalidParameter,
            "Invalid groups specified",
        ),
    ],
)
def test_context_rejects_missing_chat_or_unknown_group(
    monkeypatch, info, params, error, match
):
    _patch_http(monkeypatch)
    with pytest.raises(error, match=match):
        _loop(info=info, **params)._build_context()


def _filters(*, request=None, is_replay=False, start_time=None):
    return _build_message_filters(
        ChatRequest(url=URL, **(request or {})),
        is_replay=is_replay,
        start_time=start_time,
        end_time=None,
        offset=None,
    )


@pytest.mark.parametrize("context", [False, True])
def test_explicit_message_types_override_groups(monkeypatch, context):
    params = {"message_groups": ["messages"], "message_types": ["paid_message"]}
    if context:
        _patch_http(monkeypatch)
        msg_filter = _loop(**params)._build_context().msg_filter
    else:
        msg_filter, _ = _filters(request=params)
    assert msg_filter.should_add({"message_type": "paid_message"})
    assert not msg_filter.should_add({"message_type": "text_message"})


@pytest.mark.parametrize("replay", [False, True])
def test_continuation_urls(replay):
    page = "live_chat_replay" if replay else "live_chat"
    init_page, url = _build_continuation_urls("TOKEN", "KEY", is_replay=replay)
    assert "continuation=TOKEN" in init_page
    assert f"{page}?" in init_page
    assert f"get_{page}?key=KEY" in url
    assert ("replay" in url) == replay
    assert ("replay" in init_page) == replay


@pytest.mark.parametrize("params", [{}, {"message_groups": "messages"}])
def test_live_filter_defaults(params):
    msg_filter, time_filter = _filters(request=params)
    assert time_filter is None
    assert msg_filter is not None


def test_replay_filter_skips_pre_range_across_pages():
    _, time_filter = _filters(is_replay=True, start_time=10.0)
    assert isinstance(time_filter, TimeRangeFilter)
    assert time_filter.check({"time_in_seconds": 9.0}) == "skip"
    time_filter.end_page()
    assert time_filter.check({"time_in_seconds": 9.5}) == "skip"
    assert time_filter.check({"time_in_seconds": 10.0}) == "yield"


def test_session_headers_replace_stale_auth_preserve_custom(monkeypatch):
    _patch_http(monkeypatch, headers={"x-youtube-client-name": "1"}, innertube=False)
    owner = Downloader()
    owner.session.headers.update(
        {
            "authorization": "stale-auth",
            "x-youtube-identity-token": "stale-identity",
            "x-custom": "preserved",
        }
    )
    _loop(owner)._apply_session_headers("https://www.youtube.com/init")
    assert "content-type" in owner.session.headers
    assert "referer" in owner.session.headers
    assert "authorization" not in owner.session.headers
    assert "x-youtube-identity-token" not in owner.session.headers
    assert owner.session.headers["x-custom"] == "preserved"
