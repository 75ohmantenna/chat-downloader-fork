# SPDX-License-Identifier: MIT

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest
from requests.exceptions import RequestException

from chat_downloader.errors import RetriesExceeded
from chat_downloader.models import ChatRequest
from chat_downloader.sites.twitch import live_service


def test_live_service_iter_stream_chat_messages_retries_connection_and_reconnects() -> (
    None
):
    first_irc = Mock()
    second_irc = Mock()
    irc_factory = cast(
        "live_service._IRCFactory",
        Mock(side_effect=[OSError("temporary"), first_irc, second_irc]),
    )
    downloader = SimpleNamespace(
        badge_cache=SimpleNamespace(snapshot=dict),
        _update_badge_info=Mock(),
        retry=Mock(),
    )
    request = ChatRequest(
        url="https://www.twitch.tv/example",
        max_attempts=3,
        retry_timeout=0,
        interruptible_retry=False,
        message_receive_timeout=1.5,
        message_groups=["messages"],
    )
    message_generator = cast(
        "live_service._MessageGenerator",
        Mock(
            side_effect=[
                ConnectionError("reconnect"),
                iter(
                    [
                        {
                            "message_type": "text_message",
                            "message_id": "kept",
                            "extra": "x",
                        }
                    ],
                ),
            ],
        ),
    )

    with (
        patch.object(
            live_service,
            "build_known_irc_keys",
            return_value={"message_type", "message_id"},
        ),
        patch.object(live_service, "debug_log") as mock_debug_log,
    ):
        result = list(
            live_service.iter_stream_chat_messages(
                cast("Any", downloader),
                "example",
                request,
                irc_factory=irc_factory,
                message_generator=message_generator,
            ),
        )

    assert result == [
        {"message_type": "text_message", "message_id": "kept", "extra": "x"},
    ]
    downloader.retry.assert_called_once()
    first_irc.close_connection.assert_called_once()
    second_irc.close_connection.assert_called_once()
    mock_debug_log.assert_called_once()


def test_live_service_passes_effective_proxy_to_irc_factory() -> None:
    irc = Mock()
    irc_factory = cast("live_service._IRCFactory", Mock(return_value=irc))
    downloader = SimpleNamespace(
        badge_cache=SimpleNamespace(snapshot=dict),
        retry=Mock(),
        session=SimpleNamespace(
            proxies={"https": "socks5h://proxy.test:1080"},
            trust_env=False,
        ),
    )
    request = ChatRequest(
        url="https://www.twitch.tv/example",
        max_attempts=1,
        message_groups=["messages"],
    )

    result = list(
        live_service.iter_stream_chat_messages(
            cast("Any", downloader),
            "example",
            request,
            irc_factory=irc_factory,
            message_generator=cast(
                "live_service._MessageGenerator",
                Mock(return_value=iter(())),
            ),
        )
    )

    assert result == []
    irc_factory.assert_called_once_with(
        connect_timeout=10.0,
        proxy_url="socks5h://proxy.test:1080",
    )


def test_live_service_default_messages_include_social_sharing_badge() -> None:
    irc = Mock()
    downloader = SimpleNamespace(
        badge_cache=SimpleNamespace(snapshot=dict),
        retry=Mock(),
    )
    request = ChatRequest(
        url="https://www.twitch.tv/example",
        max_attempts=1,
        message_groups=["messages"],
    )
    social_sharing_badge = {
        "action_type": "user_notice",
        "message_type": "social_sharing_badge",
        "message_id": "social-badge-1",
    }

    result = list(
        live_service.iter_stream_chat_messages(
            cast("Any", downloader),
            "example",
            request,
            irc_factory=cast("live_service._IRCFactory", Mock(return_value=irc)),
            message_generator=cast(
                "live_service._MessageGenerator",
                Mock(return_value=iter([social_sharing_badge])),
            ),
        )
    )

    assert result == [social_sharing_badge]


def test_live_service_iter_stream_chat_messages_filters_and_logs_every_250th() -> None:
    irc = Mock()
    downloader = SimpleNamespace(
        badge_cache=SimpleNamespace(snapshot=dict),
        retry=Mock(),
    )
    request = ChatRequest(
        url="https://www.twitch.tv/example",
        max_attempts=2,
        retry_timeout=0,
        interruptible_retry=False,
        message_groups=["messages"],
    )
    messages = [
        {"message_type": "text_message", "message_id": str(index)}
        for index in range(251)
    ]
    fake_filter = Mock()
    fake_filter.should_add.side_effect = [False] + [True] * 250

    with (
        patch.object(live_service, "log") as mock_log,
        patch.object(
            live_service.MessageFilter,
            "from_request",
            return_value=fake_filter,
        ),
    ):
        result = list(
            live_service.iter_stream_chat_messages(
                cast("Any", downloader),
                "example",
                request,
                irc_factory=cast("live_service._IRCFactory", Mock(return_value=irc)),
                message_generator=cast(
                    "live_service._MessageGenerator",
                    Mock(return_value=iter(messages)),
                ),
            ),
        )

    assert len(result) == 250
    mock_log.assert_any_call("debug", "Total number of messages: 250")


def test_live_service_iter_stream_chat_messages_reconnects_on_reconnect_message() -> (
    None
):
    first_irc = Mock()
    second_irc = Mock()
    downloader = SimpleNamespace(
        badge_cache=SimpleNamespace(snapshot=dict),
        _update_badge_info=Mock(),
        retry=Mock(),
    )
    request = ChatRequest(
        url="https://www.twitch.tv/example",
        max_attempts=2,
        retry_timeout=0,
        interruptible_retry=False,
        message_groups=["messages", "other"],
    )
    message_generator = cast(
        "live_service._MessageGenerator",
        Mock(
            side_effect=[
                iter([{"action_type": "reconnect", "message_type": "reconnect"}]),
                iter([{"message_type": "text_message", "message_id": "kept"}]),
            ],
        ),
    )

    with patch.object(live_service, "log") as mock_log:
        result = list(
            live_service.iter_stream_chat_messages(
                cast("Any", downloader),
                "example",
                request,
                irc_factory=cast(
                    "live_service._IRCFactory",
                    Mock(side_effect=[first_irc, second_irc]),
                ),
                message_generator=message_generator,
            ),
        )

    assert result == [{"message_type": "text_message", "message_id": "kept"}]
    first_irc.close_connection.assert_called_once()
    second_irc.close_connection.assert_called_once()
    mock_log.assert_any_call(
        "info",
        "Twitch IRC server requested reconnect; reconnecting.",
    )


def test_live_service_iter_stream_chat_messages_deduplicates_by_message_id() -> None:
    irc = Mock()
    downloader = SimpleNamespace(
        badge_cache=SimpleNamespace(snapshot=dict),
        retry=Mock(),
    )
    request = ChatRequest(
        url="https://www.twitch.tv/example",
        max_attempts=1,
        message_groups=["messages"],
    )
    messages = [
        {
            "message_type": "text_message",
            "message_id": "dup",
            "message": "first",
        },
        {
            "message_type": "text_message",
            "message_id": "dup",
            "message": "duplicate",
        },
        {
            "message_type": "text_message",
            "message_id": "unique",
            "message": "second",
        },
    ]

    result = list(
        live_service.iter_stream_chat_messages(
            cast("Any", downloader),
            "example",
            request,
            irc_factory=cast("live_service._IRCFactory", Mock(return_value=irc)),
            message_generator=cast(
                "live_service._MessageGenerator",
                Mock(return_value=iter(messages)),
            ),
        ),
    )

    assert result == [
        {
            "message_type": "text_message",
            "message_id": "dup",
            "message": "first",
        },
        {
            "message_type": "text_message",
            "message_id": "unique",
            "message": "second",
        },
    ]


def test_live_service_iter_stream_chat_messages_rejects_zero_attempts() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        ChatRequest(
            url="https://www.twitch.tv/example",
            max_attempts=0,
            message_groups=["messages"],
        )


def test_live_service_get_chat_by_stream_id_handles_rerun_and_updates_badges(
    caplog,
) -> None:
    downloader = SimpleNamespace(
        _download_gql=Mock(
            return_value=[
                {
                    "data": {
                        "user": {
                            "stream": {"type": "rerun"},
                            "lastBroadcast": {"title": "Rerun Title"},
                        },
                    },
                },
            ],
        ),
        _update_badge_info=Mock(),
        _get_chat_messages_by_stream_id=Mock(return_value=iter(())),
        retry=Mock(),
    )

    with caplog.at_level(logging.INFO, logger=live_service.logger.name):
        chat = live_service.get_chat_by_stream_id(
            cast("Any", downloader),
            "example",
            ChatRequest(url="https://www.twitch.tv/example", max_attempts=1),
        )

    assert chat.title == "Rerun Title"
    assert chat.status == "live"
    downloader._update_badge_info.assert_called_once_with("example")
    assert any("broadcasting a rerun" in r.message for r in caplog.records)


def test_live_service_get_chat_by_stream_id_rejects_zero_attempts() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        ChatRequest(url="https://www.twitch.tv/missing-channel", max_attempts=0)


def test_live_service_get_chat_by_stream_id_retries_then_raises_user_not_found() -> (
    None
):
    downloader = SimpleNamespace(
        _download_gql=Mock(
            side_effect=[
                RequestException("temporary"),
                [{"data": {"user": None}}],
            ],
        ),
        _update_badge_info=Mock(),
        _get_chat_messages_by_stream_id=Mock(return_value=iter(())),
        retry=Mock(),
    )

    with pytest.raises(live_service.UserNotFound):
        live_service.get_chat_by_stream_id(
            cast("Any", downloader),
            "missing-channel",
            ChatRequest(url="https://www.twitch.tv/missing-channel", max_attempts=2),
        )

    downloader.retry.assert_called_once()
    downloader._update_badge_info.assert_not_called()


def test_live_service_get_chat_by_stream_id_marks_offline_stream_upcoming(
    caplog,
) -> None:
    downloader = SimpleNamespace(
        _download_gql=Mock(
            return_value=[
                {
                    "data": {
                        "user": {
                            "stream": {"type": None},
                            "lastBroadcast": {"title": "Ignored"},
                        },
                    },
                },
            ],
        ),
        _update_badge_info=Mock(),
        _get_chat_messages_by_stream_id=Mock(return_value=iter(())),
        retry=Mock(),
    )

    with caplog.at_level(logging.WARNING, logger="chat_downloader"):
        chat = live_service.get_chat_by_stream_id(
            cast("Any", downloader),
            "offline-channel",
            ChatRequest(url="https://www.twitch.tv/offline-channel", max_attempts=1),
        )

    assert chat.title == "offline-channel"
    assert chat.status == "upcoming"
    assert any("not currently live" in r.message for r in caplog.records)


def test_live_service_get_chat_by_stream_id_retries_on_key_error_from_gql_schema_change() -> (  # noqa: E501
    None
):
    """KeyError from unexpected GQL schema is retried, not surfaced."""
    downloader = SimpleNamespace(
        _download_gql=Mock(
            side_effect=[
                [{}],  # missing "data" key → KeyError on [0]["data"]["user"]
                [
                    {
                        "data": {
                            "user": {
                                "stream": {"type": "live"},
                                "lastBroadcast": {"title": "Live Title"},
                            },
                        },
                    },
                ],
            ],
        ),
        _update_badge_info=Mock(),
        _get_chat_messages_by_stream_id=Mock(return_value=iter(())),
        retry=Mock(),
    )

    chat = live_service.get_chat_by_stream_id(
        cast("Any", downloader),
        "example",
        ChatRequest(url="https://www.twitch.tv/example", max_attempts=2),
    )

    assert chat.title == "Live Title"
    downloader.retry.assert_called_once()


def test_live_service_reconnect_refreshes_badge_set() -> None:
    """Reconnect must call _update_badge_info and take a fresh snapshot."""
    first_irc = Mock()
    second_irc = Mock()

    initial_badges = {"old": True}
    refreshed_badges = {"old": True, "new": True}

    badge_cache = SimpleNamespace(
        snapshot=Mock(side_effect=[initial_badges, refreshed_badges])
    )
    downloader = SimpleNamespace(
        badge_cache=badge_cache,
        _update_badge_info=Mock(),
        retry=Mock(),
    )
    request = ChatRequest(
        url="https://www.twitch.tv/example",
        max_attempts=2,
        retry_timeout=0,
        interruptible_retry=False,
        message_receive_timeout=1.5,
        message_groups=["messages"],
    )

    captured_badge_sets: list[Any] = []

    def message_generator(irc, channel, params, badge_set):
        captured_badge_sets.append(badge_set)
        if irc is first_irc:
            raise ConnectionError("reconnect")
        yield from [{"message_type": "text_message", "message_id": "kept"}]

    result = list(
        live_service.iter_stream_chat_messages(
            cast("Any", downloader),
            "example",
            request,
            irc_factory=cast(
                "live_service._IRCFactory",
                Mock(side_effect=[first_irc, second_irc]),
            ),
            message_generator=cast("live_service._MessageGenerator", message_generator),
        ),
    )

    assert result == [{"message_type": "text_message", "message_id": "kept"}]
    downloader._update_badge_info.assert_called_once_with("example")
    # Second call should have the refreshed badge set
    assert captured_badge_sets[1] is refreshed_badges


def test_is_duplicate_live_message_ignores_invalid_message_ids() -> None:
    seen_message_cache = live_service._SeenMessageCache(limit=2)

    assert (
        live_service._is_duplicate_live_message(
            None,
            seen_message_cache,
        )
        is False
    )
    assert (
        live_service._is_duplicate_live_message(
            "",
            seen_message_cache,
        )
        is False
    )
    assert list(seen_message_cache.message_ids) == []


def test_is_duplicate_live_message_evicts_oldest_seen_message() -> None:
    oldest = "message-0"
    seen_message_cache = live_service._SeenMessageCache(limit=2)

    assert live_service._is_duplicate_live_message(oldest, seen_message_cache) is False
    assert live_service._is_duplicate_live_message(oldest, seen_message_cache) is True
    assert (
        live_service._is_duplicate_live_message("message-1", seen_message_cache)
        is False
    )
    assert (
        live_service._is_duplicate_live_message("newest", seen_message_cache) is False
    )
    assert oldest not in seen_message_cache.message_ids
    assert "newest" in seen_message_cache.message_ids


def test_live_service_iter_stream_chat_messages_raises_runtime_error_if_retry_returns() -> (  # noqa: E501
    None
):
    downloader = SimpleNamespace(
        badge_cache=SimpleNamespace(snapshot=dict),
        retry=Mock(),
    )
    request = ChatRequest(
        url="https://www.twitch.tv/example",
        max_attempts=1,
        message_groups=["messages"],
    )

    with (
        patch.object(live_service, "_attempt_numbers", return_value=iter([1])),
        pytest.raises(RuntimeError, match="unreachable"),
    ):
        list(
            live_service.iter_stream_chat_messages(
                cast("Any", downloader),
                "example",
                request,
                irc_factory=cast(
                    "live_service._IRCFactory",
                    Mock(side_effect=OSError("temporary")),
                ),
            )
        )


def test_live_service_repeated_disconnects_exhaust_reconnect_budget() -> None:
    ircs = [Mock(), Mock()]
    downloader = SimpleNamespace(
        badge_cache=SimpleNamespace(snapshot=dict),
        _update_badge_info=Mock(),
        retry=Mock(),
    )
    request = ChatRequest(
        url="https://www.twitch.tv/example",
        max_attempts=2,
        retry_timeout=0,
        interruptible_retry=False,
        message_groups=["messages"],
    )

    with pytest.raises(RetriesExceeded):
        list(
            live_service.iter_stream_chat_messages(
                cast("Any", downloader),
                "example",
                request,
                irc_factory=cast("live_service._IRCFactory", Mock(side_effect=ircs)),
                message_generator=cast(
                    "live_service._MessageGenerator",
                    Mock(
                        side_effect=[
                            ConnectionError("drop one"),
                            ConnectionError("drop two"),
                        ]
                    ),
                ),
            )
        )

    assert all(irc.close_connection.called for irc in ircs)


def test_live_service_closes_partial_connection_when_join_fails() -> None:
    failed_irc = Mock()
    failed_irc.join_channel.side_effect = OSError("join failed")
    healthy_irc = Mock()
    downloader = SimpleNamespace(
        badge_cache=SimpleNamespace(snapshot=dict),
        retry=Mock(),
    )
    request = ChatRequest(
        url="https://www.twitch.tv/example",
        max_attempts=2,
        retry_timeout=0,
        interruptible_retry=False,
        message_groups=["messages"],
    )

    assert (
        list(
            live_service.iter_stream_chat_messages(
                cast("Any", downloader),
                "example",
                request,
                irc_factory=cast(
                    "live_service._IRCFactory",
                    Mock(side_effect=[failed_irc, healthy_irc]),
                ),
                message_generator=cast(
                    "live_service._MessageGenerator",
                    Mock(return_value=iter(())),
                ),
            )
        )
        == []
    )

    failed_irc.close_connection.assert_called_once()
