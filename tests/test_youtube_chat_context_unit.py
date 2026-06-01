# SPDX-License-Identifier: MIT

import pytest

from chat_downloader.errors import InvalidParameter, NoContinuation
from chat_downloader.models import ChatRequest
from chat_downloader.request_profiles import REQUEST_PROFILE_INNERTUBE_CONTEXTS
from chat_downloader.sites.youtube.chat_streams_runtime_iteration import (
    _build_chat_context,
)


class _DummySession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}


class _DummyDownloader:
    def __init__(self) -> None:
        self.session = _DummySession()
        self._session_post = object()
        self.invalid_type_checks: list[tuple[object, object]] = []
        self.header_updates: list[dict[str, str]] = []
        self.applied_profiles: list[str] = []
        self._request_profile = "youtube_web"
        self._auto_profile_fallback = True

    def check_for_invalid_types(self, message_types, valid_types) -> None:
        self.invalid_type_checks.append((message_types, valid_types))

    def update_session_headers(self, headers) -> None:
        self.header_updates.append(headers)
        self.session.headers.update(headers)

    def apply_request_profile(self, profile_name: str) -> bool:
        self.applied_profiles.append(profile_name)
        self._request_profile = profile_name
        return True


def _make_initial_info(
    status: str = "live", offset: float | None = None
) -> dict:
    info: dict = {
        "continuation_info": {
            "Top chat": "top-token",
            "Live chat": "live-token",
        },
        "status": status,
    }
    if offset is not None:
        info["offset"] = offset
    return info


def test_build_chat_context_returns_correct_continuation_url(
    monkeypatch,
) -> None:
    """_build_chat_context derives the API URL from ytcfg and replay status."""
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._generate_headers",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._generate_sapisidhash_header",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._get_innertube_context",
        lambda _ytcfg: {"client": {}},
    )

    downloader = _DummyDownloader()
    ctx = _build_chat_context(
        downloader,
        _make_initial_info(status="live"),
        {"INNERTUBE_API_KEY": "testkey"},
        ChatRequest(
            url="https://www.youtube.com/watch?v=abc", chat_type="live"
        ),
    )

    assert "testkey" in ctx.continuation_url
    assert "live_chat" in ctx.continuation_url
    assert "replay" not in ctx.continuation_url
    assert ctx.is_replay is False


def test_build_chat_context_replay_status_sets_is_replay(monkeypatch) -> None:
    """_build_chat_context sets is_replay=True and uses the replay endpoint."""
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._generate_headers",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._generate_sapisidhash_header",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._get_innertube_context",
        lambda _ytcfg: {"client": {}},
    )

    downloader = _DummyDownloader()
    ctx = _build_chat_context(
        downloader,
        _make_initial_info(status="past"),
        {"INNERTUBE_API_KEY": "testkey"},
        ChatRequest(
            url="https://www.youtube.com/watch?v=abc", chat_type="live"
        ),
    )

    assert ctx.is_replay is True
    assert "live_chat_replay" in ctx.continuation_url


def test_build_chat_context_selects_chat_type_by_label_not_insertion_order(
    monkeypatch,
) -> None:
    """_build_chat_context should not assume submenu insertion order."""
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._generate_headers",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._generate_sapisidhash_header",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._get_innertube_context",
        lambda _ytcfg: {"client": {}},
    )

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
        ChatRequest(url="https://www.youtube.com/watch?v=abc", chat_type="top"),
    )

    assert ctx.loop_state.continuation == "top-token"


def test_build_chat_context_applies_request_profile_to_innertube_context(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._generate_headers",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._generate_sapisidhash_header",
        lambda *_a, **_k: None,
    )

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
        ChatRequest(
            url="https://www.youtube.com/watch?v=abc", chat_type="live"
        ),
    )

    assert ctx.innertube_context["client"]["clientName"] == "ANDROID"
    assert (
        ctx.innertube_context["client"]["androidSdkVersion"]
        == REQUEST_PROFILE_INNERTUBE_CONTEXTS["youtube_android"]["client"][
            "androidSdkVersion"
        ]
    )


def test_build_chat_context_raises_no_continuation_for_missing_index(
    monkeypatch,
) -> None:
    """_build_chat_context raises NoContinuation when chat_type is absent."""
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._generate_headers",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._generate_sapisidhash_header",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._get_innertube_context",
        lambda _ytcfg: {"client": {}},
    )

    downloader = _DummyDownloader()
    with pytest.raises(NoContinuation):
        _build_chat_context(
            downloader,
            {"continuation_info": {"Top chat": "only-one"}, "status": "live"},
            {"INNERTUBE_API_KEY": "key"},
            ChatRequest(
                url="https://www.youtube.com/watch?v=abc",
                chat_type="live",
            ),
        )


def test_build_chat_context_raises_invalid_parameter_for_bad_group(
    monkeypatch,
) -> None:
    """_build_chat_context raises InvalidParameter for an unknown group."""
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._generate_headers",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._generate_sapisidhash_header",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._get_innertube_context",
        lambda _ytcfg: {"client": {}},
    )

    downloader = _DummyDownloader()
    with pytest.raises(InvalidParameter, match="Invalid groups specified"):
        _build_chat_context(
            downloader,
            _make_initial_info(status="live"),
            {"INNERTUBE_API_KEY": "key"},
            ChatRequest(
                url="https://www.youtube.com/watch?v=abc",
                message_groups=["no-such-group"],
            ),
        )


def test_build_chat_context_message_types_override_default_groups(
    monkeypatch,
) -> None:
    """Explicit message types exclude resolved site-default groups."""
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._generate_headers",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._generate_sapisidhash_header",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._get_innertube_context",
        lambda _ytcfg: {"client": {}},
    )

    downloader = _DummyDownloader()
    ctx = _build_chat_context(
        downloader,
        _make_initial_info(status="live"),
        {"INNERTUBE_API_KEY": "key"},
        ChatRequest(
            url="https://www.youtube.com/watch?v=abc",
            message_groups=["messages"],
            message_types=["paid_message"],
        ),
    )

    assert ctx.msg_filter.should_add({"message_type": "paid_message"})
    assert not ctx.msg_filter.should_add({"message_type": "text_message"})
