# SPDX-License-Identifier: MIT

"""Unit tests for chat_downloader.models (DownloaderConfig, ChatRequest).

These tests exercise:
- DownloaderConfig construction and as_dict()
- ChatRequest construction with defaults
- ChatRequest.from_kwargs() round-trip and unknown-key behavior
- ChatRequest.as_dict()/to_legacy_kwargs() key completeness and value fidelity
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest

from chat_downloader.models import (
    DEFAULT_BUFFER_SIZE,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_SEEN_MESSAGE_IDS,
    DEFAULT_MESSAGE_RECEIVE_TIMEOUT,
    ChatRequest,
    DownloaderConfig,
    RunConfig,
    SiteDefault,
    coerce_chat_request,
    get_field_default,
)
from chat_downloader.sites.models import SiteDefault as CompatSiteDefault

# ---------------------------------------------------------------------------
# Expected legacy-kwargs keys (must match get_chat() parameter list exactly)
# ---------------------------------------------------------------------------
_EXPECTED_LEGACY_KEYS = frozenset(
    {
        "url",
        "start_time",
        "end_time",
        "max_attempts",
        "retry_timeout",
        "interruptible_retry",
        "timeout",
        "inactivity_timeout",
        "max_messages",
        "message_groups",
        "message_types",
        "output",
        "overwrite",
        "sort_keys",
        "format",
        "format_file",
        "chat_type",
        "ignore",
        "youtube_replay_poll_interval",
        "message_receive_timeout",
        "buffer_size",
    },
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_request() -> ChatRequest:
    return ChatRequest(url="https://example.com/watch?v=abc")


@pytest.fixture
def sample_request_dict() -> dict:
    return ChatRequest(url="https://youtube.com/watch?v=test123").as_dict()


# ===========================================================================
# DownloaderConfig
# ===========================================================================


def test_downloader_config_defaults() -> None:
    cfg = DownloaderConfig()
    assert cfg.headers is None
    assert cfg.cookies is None
    assert cfg.proxy is None


def test_downloader_config_explicit_values() -> None:
    headers = {"User-Agent": "TestBot/1.0"}
    cfg = DownloaderConfig(
        headers=headers,
        cookies="/tmp/cookies.txt",
        proxy="socks5://127.0.0.1:1080",
    )
    assert cfg.headers == headers
    assert cfg.cookies == "/tmp/cookies.txt"
    assert cfg.proxy == "socks5://127.0.0.1:1080"


@pytest.mark.parametrize("profile", ["unknown", "YOUTUBE_WEB", 7])
def test_downloader_config_rejects_invalid_request_profile(profile: object) -> None:
    with pytest.raises(ValueError, match="request_profile must be one of"):
        DownloaderConfig(request_profile=profile)  # type: ignore[arg-type]


def test_downloader_config_as_dict_keys() -> None:
    d = DownloaderConfig().as_dict()
    assert set(d.keys()) == {
        "headers",
        "cookies",
        "proxy",
        "connect_timeout",
        "read_timeout",
        "request_profile",
        "auto_profile_fallback",
        "twitch_client_id",
    }


def test_downloader_config_as_dict_values_match_fields() -> None:
    headers = {"Accept": "application/json"}
    cfg = DownloaderConfig(headers=headers, cookies="c.txt", proxy="http://proxy:8080")
    d = cfg.as_dict()
    assert d["headers"] == headers
    assert d["cookies"] == "c.txt"
    assert d["proxy"] == "http://proxy:8080"


def test_downloader_config_as_dict_none_values() -> None:
    d = DownloaderConfig().as_dict()
    assert "headers" in d
    assert d["headers"] is None
    assert d["cookies"] is None
    assert d["proxy"] is None


def test_downloader_config_as_dict_is_a_copy() -> None:
    cfg = DownloaderConfig(proxy="http://p:8080")
    d = cfg.as_dict()
    d["proxy"] = "mutated"
    assert cfg.proxy == "http://p:8080"


def test_get_field_default_returns_none_for_required_dataclass_field() -> None:
    @dataclasses.dataclass
    class _RequiredOnly:
        required: int

    field = _RequiredOnly.__dataclass_fields__["required"]
    assert get_field_default(field) is None


# ===========================================================================
# ChatRequest — defaults
# ===========================================================================


def test_chat_request_url(sample_request: ChatRequest) -> None:
    assert sample_request.url == "https://example.com/watch?v=abc"


def test_coerce_chat_request_returns_existing_request() -> None:
    request = ChatRequest(url="https://example.com/watch?v=abc", max_messages=2)
    assert coerce_chat_request(request) is request


def test_coerce_chat_request_builds_from_legacy_kwargs() -> None:
    request = coerce_chat_request(
        {"url": "https://example.com/watch?v=def", "max_messages": 3},
    )
    assert isinstance(request, ChatRequest)
    assert request.url == "https://example.com/watch?v=def"
    assert request.max_messages == 3


def test_coerce_chat_request_rejects_unknown_kwargs() -> None:
    with pytest.raises(TypeError, match="max_messges"):
        coerce_chat_request(
            {"url": "https://example.com/watch?v=def", "max_messges": 3},
        )


def test_default_max_attempts(sample_request: ChatRequest) -> None:
    assert sample_request.max_attempts == DEFAULT_MAX_ATTEMPTS


def test_default_message_receive_timeout(sample_request: ChatRequest) -> None:
    assert sample_request.message_receive_timeout == pytest.approx(
        DEFAULT_MESSAGE_RECEIVE_TIMEOUT
    )


def test_default_buffer_size(sample_request: ChatRequest) -> None:
    assert sample_request.buffer_size == DEFAULT_BUFFER_SIZE


def test_default_overwrite(sample_request: ChatRequest) -> None:
    assert sample_request.overwrite


def test_default_sort_keys(sample_request: ChatRequest) -> None:
    assert sample_request.sort_keys


def test_default_interruptible_retry(sample_request: ChatRequest) -> None:
    assert sample_request.interruptible_retry


def test_default_chat_type(sample_request: ChatRequest) -> None:
    assert sample_request.chat_type == "live"


def test_default_message_groups_is_site_default(
    sample_request: ChatRequest,
) -> None:
    assert isinstance(sample_request.message_groups, SiteDefault)
    assert sample_request.message_groups.name == "message_groups"


def test_default_format_is_site_default(sample_request: ChatRequest) -> None:
    assert isinstance(sample_request.format, SiteDefault)
    assert sample_request.format.name == "format"


def test_site_default_compat_import_shares_identity() -> None:
    assert SiteDefault is CompatSiteDefault


@pytest.mark.parametrize(
    "attr",
    [
        "start_time",
        "end_time",
        "retry_timeout",
        "timeout",
        "inactivity_timeout",
        "max_messages",
        "message_types",
        "output",
        "format_file",
        "ignore",
        "youtube_replay_poll_interval",
    ],
)
def test_default_none_fields(sample_request: ChatRequest, attr: str) -> None:
    assert getattr(sample_request, attr) is None


def test_message_groups_instances_are_independent() -> None:
    r1 = ChatRequest(url="a")
    r2 = ChatRequest(url="b")
    assert r1.message_groups is not r2.message_groups


def test_format_instances_are_independent() -> None:
    r1 = ChatRequest(url="a")
    r2 = ChatRequest(url="b")
    assert r1.format is not r2.format


def test_with_updates_returns_modified_copy(
    sample_request: ChatRequest,
) -> None:
    updated = sample_request.with_updates(url="https://example.com/watch?v=updated")
    assert updated.url == "https://example.com/watch?v=updated"
    assert sample_request.url == "https://example.com/watch?v=abc"
    assert updated is not sample_request


def test_resolved_for_site_returns_modified_copy(
    sample_request: ChatRequest,
) -> None:
    site = SimpleNamespace(
        get_site_value=lambda value: (
            "resolved" if isinstance(value, SiteDefault) else value
        ),
    )
    resolved = sample_request.resolved_for_site(site)
    assert resolved.message_groups == "resolved"
    assert resolved.format == "resolved"
    assert resolved.url == sample_request.url
    assert resolved is not sample_request


# ===========================================================================
# ChatRequest — from_kwargs
# ===========================================================================


def test_from_kwargs_known_key_mapped() -> None:
    req = ChatRequest.from_kwargs(url="https://twitch.tv/test", max_messages=50)
    assert req.url == "https://twitch.tv/test"
    assert req.max_messages == 50


def test_from_kwargs_round_trip_all_fields() -> None:
    original = ChatRequest(
        url="https://youtube.com/watch?v=XYZ",
        start_time=10,
        end_time=60,
        max_messages=200,
        max_attempts=5,
        retry_timeout=2.0,
        interruptible_retry=False,
        timeout=30.0,
        inactivity_timeout=5.0,
        message_types=["text_message"],
        output="chat.jsonl",
        overwrite=False,
        sort_keys=False,
        format_file="custom.json",
        chat_type="top",
        ignore=["baduser"],
        youtube_replay_poll_interval=0.75,
        message_receive_timeout=0.5,
        buffer_size=8192,
    )
    rebuilt = ChatRequest.from_kwargs(**original.as_dict())
    for fname in [
        "url",
        "start_time",
        "end_time",
        "max_messages",
        "max_attempts",
        "retry_timeout",
        "interruptible_retry",
        "timeout",
        "inactivity_timeout",
        "message_types",
        "output",
        "overwrite",
        "sort_keys",
        "format_file",
        "chat_type",
        "ignore",
        "youtube_replay_poll_interval",
        "message_receive_timeout",
        "buffer_size",
    ]:
        assert getattr(original, fname) == getattr(rebuilt, fname), (
            f"Field '{fname}' mismatch after round-trip"
        )


def test_from_kwargs_empty_call_uses_defaults() -> None:
    req = ChatRequest.from_kwargs()
    assert req.url == ""
    assert req.max_attempts == DEFAULT_MAX_ATTEMPTS


# ===========================================================================
# ChatRequest — as_dict
# ===========================================================================


def test_as_dict_returns_dict(sample_request_dict: dict) -> None:
    assert isinstance(sample_request_dict, dict)


def test_as_dict_key_set_matches_get_chat_params(
    sample_request_dict: dict,
) -> None:
    assert set(sample_request_dict.keys()) == _EXPECTED_LEGACY_KEYS


def test_as_dict_url_value(sample_request_dict: dict) -> None:
    assert sample_request_dict["url"] == "https://youtube.com/watch?v=test123"


def test_as_dict_default_values_preserved(sample_request_dict: dict) -> None:
    assert sample_request_dict["max_attempts"] == DEFAULT_MAX_ATTEMPTS
    assert sample_request_dict["buffer_size"] == DEFAULT_BUFFER_SIZE
    assert sample_request_dict["message_receive_timeout"] == pytest.approx(
        DEFAULT_MESSAGE_RECEIVE_TIMEOUT
    )
    assert sample_request_dict["overwrite"]
    assert sample_request_dict["sort_keys"]
    assert sample_request_dict["interruptible_retry"]
    assert sample_request_dict["chat_type"] == "live"


def test_as_dict_message_groups_is_site_default(
    sample_request_dict: dict,
) -> None:
    assert isinstance(sample_request_dict["message_groups"], SiteDefault)


def test_as_dict_format_is_site_default(sample_request_dict: dict) -> None:
    assert isinstance(sample_request_dict["format"], SiteDefault)


@pytest.mark.parametrize(
    "key", ["start_time", "end_time", "max_messages", "output", "format_file"]
)
def test_as_dict_none_values_present(sample_request_dict: dict, key: str) -> None:
    assert sample_request_dict[key] is None


def test_as_dict_mutation_does_not_affect_request() -> None:
    req = ChatRequest(url="https://youtube.com/watch?v=test123")
    d = req.as_dict()
    d["url"] = "mutated"
    assert req.url == "https://youtube.com/watch?v=test123"


def test_as_dict_explicit_values_passed_through() -> None:
    req = ChatRequest(
        url="https://twitch.tv/channel",
        max_messages=100,
        output=["out.jsonl", "out.txt"],
        chat_type="top",
        buffer_size=2048,
    )
    d = req.as_dict()
    assert d["url"] == "https://twitch.tv/channel"
    assert d["max_messages"] == 100
    assert d["output"] == ["out.jsonl", "out.txt"]
    assert d["chat_type"] == "top"
    assert d["buffer_size"] == 2048


def test_retry_kwargs_contains_only_retry_fields() -> None:
    req = ChatRequest(
        url="https://twitch.tv/channel",
        max_attempts=7,
        retry_timeout=2.5,
        interruptible_retry=False,
        max_messages=100,
    )
    assert req.retry_kwargs() == {
        "max_attempts": 7,
        "retry_timeout": 2.5,
        "interruptible_retry": False,
    }


# ===========================================================================
# RunConfig
# ===========================================================================


def test_run_config_defaults() -> None:
    cfg = RunConfig()
    assert cfg.quiet is False
    assert cfg.max_seen_message_ids == DEFAULT_MAX_SEEN_MESSAGE_IDS
    assert cfg.exit_on_debug is False
    assert cfg.pause_on_debug is False


def test_run_config_from_kwargs_filters_unknown_keys() -> None:
    cfg = RunConfig.from_kwargs(
        quiet=True,
        max_seen_message_ids=321,
        exit_on_debug=True,
        pause_on_debug=False,
        unknown_key="ignored",
    )
    assert cfg.quiet is True
    assert cfg.max_seen_message_ids == 321
    assert cfg.exit_on_debug is True
    assert cfg.pause_on_debug is False


def test_run_config_as_dict_contains_only_run_fields() -> None:
    cfg = RunConfig(quiet=True, max_seen_message_ids=50, exit_on_debug=True)
    assert cfg.as_dict() == {
        "quiet": True,
        "max_seen_message_ids": 50,
        "exit_on_debug": True,
        "pause_on_debug": False,
    }


# ===========================================================================
# Constants
# ===========================================================================


def test_default_max_attempts_positive() -> None:
    assert DEFAULT_MAX_ATTEMPTS > 0


def test_default_buffer_size_positive() -> None:
    assert DEFAULT_BUFFER_SIZE > 0


def test_default_message_receive_timeout_positive() -> None:
    assert DEFAULT_MESSAGE_RECEIVE_TIMEOUT > 0


# ===========================================================================
# ChatRequest — from_kwargs strict mode
# ===========================================================================


def test_from_kwargs_non_strict_ignores_unknown() -> None:
    req = ChatRequest.from_kwargs(
        url="https://youtube.com/watch?v=x",
        not_a_real_param="oops",
        another_bogus=42,
    )
    assert req.url == "https://youtube.com/watch?v=x"
    assert not hasattr(req, "not_a_real_param")
    assert not hasattr(req, "another_bogus")


def test_from_kwargs_strict_raises_on_unknown() -> None:
    with pytest.raises(TypeError) as ctx:
        ChatRequest.from_kwargs(
            strict=True,
            url="https://youtube.com/watch?v=x",
            totally_unknown="bad",
        )
    msg = str(ctx.value)
    assert "totally_unknown" in msg
    assert "unknown keyword argument" in msg


def test_from_kwargs_strict_raises_lists_all_unknown_keys() -> None:
    with pytest.raises(TypeError) as ctx:
        ChatRequest.from_kwargs(
            strict=True,
            url="https://youtube.com/watch?v=x",
            alpha="a",
            beta=2,
        )
    msg = str(ctx.value)
    assert "alpha" in msg
    assert "beta" in msg


def test_from_kwargs_strict_passes_when_all_keys_known() -> None:
    req = ChatRequest.from_kwargs(
        strict=True, url="https://twitch.tv/channel", max_messages=10
    )
    assert req.url == "https://twitch.tv/channel"
    assert req.max_messages == 10


def test_from_kwargs_strict_empty_call_passes() -> None:
    req = ChatRequest.from_kwargs(strict=True)
    assert req.url == ""


def test_from_kwargs_strict_does_not_treat_strict_as_field() -> None:
    req = ChatRequest.from_kwargs(strict=True, url="https://youtube.com/watch?v=z")
    assert not hasattr(req, "strict")


# ===========================================================================
# ChatRequest — __post_init__ validation
# ===========================================================================


def test_valid_defaults() -> None:
    req = ChatRequest()
    assert req.max_messages is None
    assert req.max_attempts >= 1
    assert req.buffer_size > 0
    assert req.chat_type == "live"


def test_max_messages_none_allowed() -> None:
    assert ChatRequest(max_messages=None).max_messages is None


def test_max_messages_positive_int_allowed() -> None:
    assert ChatRequest(max_messages=1).max_messages == 1


@pytest.mark.parametrize("max_messages", [0, -1])
def test_max_messages_invalid_raises(max_messages: int) -> None:
    with pytest.raises(ValueError, match="max_messages"):
        ChatRequest(max_messages=max_messages)


def test_max_attempts_one_allowed() -> None:
    assert ChatRequest(max_attempts=1).max_attempts == 1


@pytest.mark.parametrize("max_attempts", [0, -5])
def test_max_attempts_invalid_raises(max_attempts: int) -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        ChatRequest(max_attempts=max_attempts)


def test_buffer_size_positive_allowed() -> None:
    assert ChatRequest(buffer_size=1).buffer_size == 1


@pytest.mark.parametrize("buffer_size", [0, -1])
def test_buffer_size_invalid_raises(buffer_size: int) -> None:
    with pytest.raises(ValueError, match="buffer_size"):
        ChatRequest(buffer_size=buffer_size)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("max_messages", True),
        ("max_attempts", 1.5),
        ("max_attempts", True),
        ("buffer_size", 1.5),
        ("buffer_size", True),
        ("retry_timeout", "manual"),
        ("retry_timeout", True),
        ("retry_timeout", float("nan")),
        ("timeout", "forever"),
        ("inactivity_timeout", True),
        ("message_receive_timeout", "slow"),
    ],
)
def test_request_numeric_fields_reject_wrong_runtime_values(
    field_name: str,
    value: Any,
) -> None:
    with pytest.raises(ValueError, match=field_name):
        ChatRequest(**{field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("start_time", "not-a-time"),
        ("start_time", "1:not-a-number"),
        ("start_time", True),
        ("end_time", float("nan")),
        ("end_time", float("inf")),
    ],
)
def test_request_time_bounds_reject_malformed_values(
    field_name: str,
    value: Any,
) -> None:
    with pytest.raises(ValueError, match=field_name):
        ChatRequest(**{field_name: value})


@pytest.mark.parametrize("chat_type", ["live", "top"])
def test_chat_type_allowed(chat_type: str) -> None:
    assert ChatRequest(chat_type=chat_type).chat_type == chat_type  # type: ignore[arg-type]


@pytest.mark.parametrize("chat_type", ["invalid", ""])
def test_chat_type_invalid_raises(chat_type: str) -> None:
    with pytest.raises(ValueError, match="chat_type"):
        ChatRequest(chat_type=chat_type)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ChatRequest — timeout field validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [0.0, -1.0, float("nan"), float("inf"), float("-inf")],
)
def test_timeout_invalid_raises(value: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        ChatRequest(timeout=value)


def test_timeout_none_allowed() -> None:
    assert ChatRequest(timeout=None).timeout is None


def test_timeout_positive_allowed() -> None:
    assert ChatRequest(timeout=30.0).timeout == pytest.approx(30.0)


@pytest.mark.parametrize(
    "value",
    [0.0, -1.0, float("nan"), float("inf"), float("-inf")],
)
def test_inactivity_timeout_invalid_raises(value: float) -> None:
    with pytest.raises(ValueError, match="inactivity_timeout"):
        ChatRequest(inactivity_timeout=value)


def test_inactivity_timeout_none_allowed() -> None:
    assert ChatRequest(inactivity_timeout=None).inactivity_timeout is None


@pytest.mark.parametrize(
    "value",
    [0.0, -1.0, float("nan"), float("inf"), float("-inf")],
)
def test_message_receive_timeout_invalid_raises(value: float) -> None:
    with pytest.raises(ValueError, match="message_receive_timeout"):
        ChatRequest(message_receive_timeout=value)


def test_message_receive_timeout_positive_allowed() -> None:
    assert ChatRequest(
        message_receive_timeout=0.5
    ).message_receive_timeout == pytest.approx(0.5)


@pytest.mark.parametrize(
    "value",
    [0.0, 0.49, 8.01, float("nan"), float("inf"), float("-inf")],
)
def test_youtube_replay_poll_interval_invalid_raises(value: float) -> None:
    with pytest.raises(ValueError, match="youtube_replay_poll_interval"):
        ChatRequest(youtube_replay_poll_interval=value)


@pytest.mark.parametrize("value", [None, 0.5, 1.0, 8.0])
def test_youtube_replay_poll_interval_allowed(value: float | None) -> None:
    actual = ChatRequest(
        youtube_replay_poll_interval=value
    ).youtube_replay_poll_interval
    assert actual == value


# ---------------------------------------------------------------------------
# DownloaderConfig — timeout field validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("connect_timeout", 0.0),
        ("connect_timeout", -1.0),
        ("connect_timeout", float("nan")),
        ("connect_timeout", float("inf")),
        ("read_timeout", 0.0),
        ("read_timeout", -5.0),
        ("read_timeout", float("nan")),
        ("read_timeout", float("inf")),
    ],
)
def test_downloader_config_timeout_invalid_raises(
    field_name: str, value: float
) -> None:
    with pytest.raises(ValueError, match=field_name):
        DownloaderConfig(**{field_name: value})


def test_downloader_config_valid_timeouts() -> None:
    cfg = DownloaderConfig(connect_timeout=5.0, read_timeout=30.0)
    assert cfg.connect_timeout == pytest.approx(5.0)
    assert cfg.read_timeout == pytest.approx(30.0)
