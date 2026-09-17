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


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("max_attempts", DEFAULT_MAX_ATTEMPTS),
        ("message_receive_timeout", DEFAULT_MESSAGE_RECEIVE_TIMEOUT),
        ("buffer_size", DEFAULT_BUFFER_SIZE),
        ("overwrite", True),
        ("sort_keys", True),
        ("interruptible_retry", True),
        ("chat_type", "live"),
    ],
)
def test_request_defaults(sample_request, name, expected) -> None:
    assert getattr(sample_request, name) == expected
    assert sample_request.as_dict()[name] == expected


@pytest.mark.parametrize("name", ["message_groups", "format"])
def test_site_defaults_are_independent(sample_request, name) -> None:
    value = getattr(sample_request, name)
    assert isinstance(value, SiteDefault)
    assert value.name == name
    assert sample_request.as_dict()[name] is value
    assert getattr(ChatRequest(), name) is not value


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
    assert rebuilt == original


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
        "resume": None,
        "verify_output": False,
        "require_complete": False,
        "run_manifest": None,
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


def test_default_message_receive_timeout_matches_provider_minimum() -> None:
    assert pytest.approx(1.0) == DEFAULT_MESSAGE_RECEIVE_TIMEOUT


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


@pytest.mark.parametrize(
    "unknown", [{"totally_unknown": "bad"}, {"alpha": "a", "beta": 2}]
)
def test_from_kwargs_strict_reports_all_unknown_keys(unknown) -> None:
    with pytest.raises(TypeError) as ctx:
        ChatRequest.from_kwargs(
            strict=True, url="https://youtube.com/watch?v=x", **unknown
        )
    for name in unknown:
        assert name in str(ctx.value)


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


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("max_messages", None),
        ("max_messages", 1),
        ("max_attempts", 1),
        ("buffer_size", 1),
        ("timeout", None),
        ("timeout", 30.0),
        ("inactivity_timeout", None),
        ("message_receive_timeout", 0.5),
    ],
)
def test_request_numeric_boundaries_allowed(name, value) -> None:
    assert getattr(ChatRequest(**{name: value}), name) == value


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("max_messages", 0),
        ("max_messages", -1),
        ("max_attempts", 0),
        ("max_attempts", -5),
        ("buffer_size", 0),
        ("buffer_size", -1),
    ],
)
def test_request_integer_boundaries_rejected(field_name, value) -> None:
    with pytest.raises(ValueError, match=field_name):
        ChatRequest(**{field_name: value})


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
    "field_name", ["timeout", "inactivity_timeout", "message_receive_timeout"]
)
@pytest.mark.parametrize(
    "value", [0.0, -1.0, float("nan"), float("inf"), float("-inf")]
)
def test_request_timeout_invalid_raises(field_name, value) -> None:
    with pytest.raises(ValueError, match=field_name):
        ChatRequest(**{field_name: value})


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
