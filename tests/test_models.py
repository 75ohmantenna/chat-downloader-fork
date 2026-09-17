# SPDX-License-Identifier: MIT
"""Construction, serialization, copy isolation, and request boundaries."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

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


@pytest.mark.parametrize(
    ("headers", "cookies", "proxy"),
    [
        (None, None, None),
        ({"User-Agent": "TestBot/1.0"}, "/tmp/cookies.txt", "socks5://127.0.0.1:1080"),
        ({"Accept": "application/json"}, "c.txt", "http://proxy:8080"),
    ],
)
def test_downloader_config_serialization(headers, cookies, proxy):
    values = {"headers": headers, "cookies": cookies, "proxy": proxy}
    cfg = DownloaderConfig(**values)
    result = cfg.as_dict()
    assert set(result) == {
        "headers",
        "cookies",
        "proxy",
        "connect_timeout",
        "read_timeout",
        "request_profile",
        "auto_profile_fallback",
        "twitch_client_id",
    }
    for name, value in values.items():
        assert getattr(cfg, name) == result[name] == value
    result["proxy"] = "mutated"
    assert cfg.proxy == values["proxy"]


def test_get_field_default_for_required_and_factory_fields():
    @dataclasses.dataclass
    class Model:
        required: int
        items: list = dataclasses.field(default_factory=list)

    required, items = dataclasses.fields(Model)
    assert get_field_default(required) is None
    assert get_field_default(items) == []
    assert isinstance(get_field_default(items), list)


@pytest.fixture
def chat_request():
    return ChatRequest(url="https://example.com/watch?v=abc")


def test_request_defaults_and_serialization(chat_request):
    request = chat_request
    expected = {
        "url": request.url,
        "max_attempts": DEFAULT_MAX_ATTEMPTS,
        "message_receive_timeout": DEFAULT_MESSAGE_RECEIVE_TIMEOUT,
        "buffer_size": DEFAULT_BUFFER_SIZE,
        "overwrite": True,
        "sort_keys": True,
        "interruptible_retry": True,
        "chat_type": "live",
        **dict.fromkeys(
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
            ]
        ),
    }
    serialized = request.as_dict()
    assert isinstance(serialized, dict)
    assert set(serialized) == {*expected, "message_groups", "format"}
    for name, value in expected.items():
        assert getattr(request, name) == serialized[name] == value
    serialized["url"] = "mutated"
    assert request.url == "https://example.com/watch?v=abc"
    assert DEFAULT_MAX_ATTEMPTS > 0
    assert DEFAULT_BUFFER_SIZE > 0
    assert pytest.approx(1.0) == DEFAULT_MESSAGE_RECEIVE_TIMEOUT


@pytest.mark.parametrize("name", ["message_groups", "format"])
def test_site_defaults_are_independent(chat_request, name):
    request = chat_request
    value = getattr(request, name)
    assert isinstance(value, SiteDefault)
    assert value.name == name
    assert request.as_dict()[name] is value
    assert getattr(ChatRequest(), name) is not value
    assert SiteDefault is CompatSiteDefault


def test_request_copy_operations(chat_request):
    request = chat_request
    updated = request.with_updates(url="updated")
    assert updated.url == "updated"
    assert request.url == "https://example.com/watch?v=abc"
    assert updated is not request
    site = SimpleNamespace(
        get_site_value=lambda value: (
            "resolved" if isinstance(value, SiteDefault) else value
        )
    )
    resolved = request.resolved_for_site(site)
    assert resolved.message_groups == resolved.format == "resolved"
    assert resolved.url == request.url
    assert resolved is not request


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize(
    "values",
    [
        {},
        {"url": "https://twitch.tv/test", "max_messages": 50},
        {
            "url": "https://youtube.com/watch?v=XYZ",
            "start_time": 10,
            "end_time": 60,
            "max_messages": 200,
            "max_attempts": 5,
            "retry_timeout": 2.0,
            "interruptible_retry": False,
            "timeout": 30.0,
            "inactivity_timeout": 5.0,
            "message_types": ["text_message"],
            "output": "chat.jsonl",
            "overwrite": False,
            "sort_keys": False,
            "format_file": "custom.json",
            "chat_type": "top",
            "ignore": ["baduser"],
            "youtube_replay_poll_interval": 0.75,
            "message_receive_timeout": 0.5,
            "buffer_size": 8192,
        },
        {
            "url": "https://twitch.tv/channel",
            "max_messages": 100,
            "output": ["out.jsonl", "out.txt"],
            "chat_type": "top",
            "buffer_size": 2048,
        },
    ],
)
def test_request_kwargs_round_trip(strict, values):
    request = ChatRequest.from_kwargs(strict=strict, **values)
    assert request == ChatRequest(**values)
    assert not hasattr(request, "strict")
    serialized = request.as_dict()
    for name, value in values.items():
        assert serialized[name] == value
    assert ChatRequest.from_kwargs(**serialized) == request


def test_coercion_preserves_requests_and_accepts_legacy_values(chat_request):
    assert coerce_chat_request(chat_request) is chat_request
    values = {"url": "https://example.com/watch?v=def", "max_messages": 3}
    assert coerce_chat_request(values) == ChatRequest(**values)
    with pytest.raises(TypeError, match="max_messges"):
        coerce_chat_request({"max_messges": 3})


@pytest.mark.parametrize(
    "unknown",
    [
        {"totally_unknown": "bad"},
        {"alpha": "a", "beta": 2},
        {"not_a_real_param": "oops", "another_bogus": 42},
    ],
)
def test_unknown_request_keys(unknown):
    request = ChatRequest.from_kwargs(url="example", **unknown)
    assert request.url == "example"
    assert all(not hasattr(request, name) for name in unknown)
    with pytest.raises(TypeError) as error:
        ChatRequest.from_kwargs(strict=True, url="example", **unknown)
    assert all(name in str(error.value) for name in unknown)


def test_retry_kwargs_contains_only_retry_fields():
    values = {"max_attempts": 7, "retry_timeout": 2.5, "interruptible_retry": False}
    assert (
        ChatRequest(url="example", max_messages=100, **values).retry_kwargs() == values
    )


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"quiet": True, "max_seen_message_ids": 321, "exit_on_debug": True},
        {"quiet": True, "max_seen_message_ids": 50, "exit_on_debug": True},
    ],
)
def test_run_config_filters_and_serializes(values):
    expected = {
        "quiet": False,
        "resume": None,
        "verify_output": False,
        "require_complete": False,
        "run_manifest": None,
        "max_seen_message_ids": DEFAULT_MAX_SEEN_MESSAGE_IDS,
        "exit_on_debug": False,
        "pause_on_debug": False,
        **values,
    }
    cfg = RunConfig.from_kwargs(unknown_key="ignored", **values)
    assert cfg == RunConfig(**values)
    assert cfg.as_dict() == expected


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
        ("chat_type", "live"),
        ("chat_type", "top"),
        *[("youtube_replay_poll_interval", value) for value in [None, 0.5, 1.0, 8.0]],
    ],
)
def test_request_boundaries_allowed(name, value):
    assert getattr(ChatRequest(**{name: value}), name) == value


@pytest.mark.parametrize(
    ("model", "field_name", "values"),
    [
        (ChatRequest, name, values)
        for name, values in [
            ("max_messages", [0, -1, True]),
            ("max_attempts", [0, -5, 1.5, True]),
            ("buffer_size", [0, -1, 1.5, True]),
            ("retry_timeout", ["manual", True, float("nan")]),
            ("timeout", ["forever"]),
            ("inactivity_timeout", [True]),
            ("message_receive_timeout", ["slow"]),
            ("start_time", ["not-a-time", "1:not-a-number", True]),
            ("end_time", [float("nan"), float("inf")]),
            ("chat_type", ["invalid", ""]),
            (
                "youtube_replay_poll_interval",
                [0.0, 0.49, 8.01, float("nan"), float("inf"), float("-inf")],
            ),
        ]
    ]
    + [
        (ChatRequest, name, [0.0, -1.0, float("nan"), float("inf"), float("-inf")])
        for name in ("timeout", "inactivity_timeout", "message_receive_timeout")
    ]
    + [
        (DownloaderConfig, name, values)
        for name, values in [
            ("request_profile", ["unknown", "YOUTUBE_WEB", 7]),
            ("connect_timeout", [0.0, -1.0, float("nan"), float("inf")]),
            ("read_timeout", [0.0, -5.0, float("nan"), float("inf")]),
        ]
    ],
)
def test_models_reject_invalid_runtime_values(model, field_name, values):
    for value in values:
        with pytest.raises(ValueError, match=field_name):
            model(**{field_name: value})


def test_downloader_config_valid_timeouts():
    cfg = DownloaderConfig(connect_timeout=5.0, read_timeout=30.0)
    assert cfg.connect_timeout == pytest.approx(5.0)
    assert cfg.read_timeout == pytest.approx(30.0)
