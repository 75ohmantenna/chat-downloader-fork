# SPDX-License-Identifier: MIT

from __future__ import annotations

import logging
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest
from requests.exceptions import RequestException

from chat_downloader.errors import RetriesExceeded
from chat_downloader.sites.twitch import live_service
from chat_downloader.sites.twitch.extractor import TwitchChatDownloader
from tests.twitch_third_helpers import chat_request


def _downloader(**overrides):
    return SimpleNamespace(
        **{
            "badge_cache": SimpleNamespace(snapshot=dict),
            "_update_badge_info": Mock(),
            "retry": Mock(),
            **overrides,
        }
    )


def _request(**overrides):
    return chat_request(
        **{
            "url": "https://www.twitch.tv/example",
            "max_attempts": 1,
            "retry_timeout": 0,
            "interruptible_retry": False,
            **overrides,
        }
    )


def _stream_downloader(*gql_effects):
    return SimpleNamespace(
        _download_gql=Mock(side_effect=list(gql_effects)),
        _update_badge_info=Mock(),
        _get_chat_messages_by_stream_id=Mock(return_value=iter(())),
        retry=Mock(),
    )


def _irc_factory(*effects):
    if not effects:
        return cast("live_service._IRCFactory", Mock(return_value=Mock()))
    return cast("live_service._IRCFactory", Mock(side_effect=list(effects)))


def _message_generator(*effects):
    """Each effect is either an exception to raise or an iterable of messages."""
    if not effects:
        return cast("live_service._MessageGenerator", Mock(return_value=iter(())))
    side_effects = [
        effect if isinstance(effect, BaseException) else iter(effect)
        for effect in effects
    ]
    return cast("live_service._MessageGenerator", Mock(side_effect=side_effects))


def _live(downloader=None, request=None, **kwargs):
    return live_service.iter_stream_chat_messages(
        cast("Any", downloader or _downloader()),
        "example",
        request or _request(),
        **kwargs,
    )


def _run_live(downloader=None, request=None, **kwargs):
    kwargs.setdefault("irc_factory", _irc_factory())
    kwargs.setdefault("message_generator", _message_generator())
    return list(_live(downloader, request, **kwargs))


def _assert_summary(diagnostics, **expected):
    assert {key: diagnostics.summary[key] for key in expected} == expected


class _FakeIRC:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.closed = False
        self.sent: list[str] = []

    def recv(self, _buffer_size: int) -> str:
        return next(self.responses)

    def send_raw(self, message: str) -> None:
        self.sent.append(message)

    def set_timeout(self, _timeout: float) -> None:
        pass

    def join_channel(self, _channel: str) -> None:
        pass

    def close_connection(self) -> None:
        self.closed = True


@pytest.fixture
def captured_frames(monkeypatch):
    captured = []

    def record_capture(*args, **kwargs):
        captured.append((args, kwargs))
        return f"/samples/{len(captured)}.json"

    monkeypatch.setattr(
        "chat_downloader.sites.twitch.irc_diagnostics.capture_debug_sample",
        record_capture,
    )
    return captured


def _privmsg(message_id: str, text: str) -> str:
    return (
        "@badge-info=;badges=;color=;display-name=User;emotes=;flags=;id="
        f"{message_id};mod=0;room-id=1;subscriber=0;tmi-sent-ts=1;turbo=0;"
        f"user-id=1;user-type= :user!user@user.tmi.twitch.tv PRIVMSG "
        f"#example :{text}"
    )


def _usernotice(message_id: str, message_type: str, text: str) -> str:
    return (
        "@badge-info=;badges=;color=;display-name=User;emotes=;flags=;id="
        f"{message_id};mod=0;msg-id={message_type};room-id=1;subscriber=1;"
        "system-msg=Event;tmi-sent-ts=1;turbo=0;user-id=1;user-type= "
        f":tmi.twitch.tv USERNOTICE #example :{text}"
    )


def _stream_metadata_payload(*, errors=None):
    result = {
        "data": {
            "user": {
                "id": "channel-123",
                "stream": {"type": "live"},
                "lastBroadcast": {"title": "Live Title"},
            }
        }
    }
    if errors is not None:
        result["errors"] = errors
    return [result]


@pytest.mark.parametrize(
    ("setup_retry", "server_reconnect"),
    [(True, False), (False, True), (False, False)],
    ids=["connection-retry", "server-reconnect", "badge-refresh"],
)
def test_live_reconnect_refreshes_badges_and_closes_connections(
    setup_retry, server_reconnect
):
    ircs = [Mock(), Mock()]
    initial_badges, refreshed_badges = {"old": True}, {"old": True, "new": True}
    snapshots = [initial_badges, refreshed_badges]
    downloader = _downloader(
        badge_cache=SimpleNamespace(snapshot=Mock(side_effect=snapshots))
    )
    diagnostics = live_service._TwitchLiveDiagnostics()
    kept = {"message_type": "text_message", "message_id": "kept", "extra": "x"}
    captured = []

    def messages(irc, channel, params, badge_set):
        captured.append(badge_set)
        if irc is ircs[0]:
            if server_reconnect:
                yield {"action_type": "reconnect", "message_type": "reconnect"}
            else:
                raise ConnectionError("reconnect")
        else:
            yield kept

    effects = ([OSError("temporary")] if setup_retry else []) + ircs
    with (
        patch.object(
            live_service,
            "build_known_irc_keys",
            return_value={"message_type", "message_id"},
        ),
        patch.object(live_service, "debug_log") as debug_log,
    ):
        result = _run_live(
            downloader,
            _request(
                max_attempts=len(effects),
                message_receive_timeout=1.5,
                message_groups=["messages", "other"],
            ),
            irc_factory=_irc_factory(*effects),
            message_generator=cast("live_service._MessageGenerator", messages),
            diagnostics=diagnostics,
        )
    assert result == [kept]
    assert downloader.retry.call_count == int(setup_retry)
    for irc in ircs:
        irc.close_connection.assert_called_once()
    debug_log.assert_called_once()
    assert captured == snapshots
    assert captured[1] is refreshed_badges
    downloader._update_badge_info.assert_called_once_with("example")
    _assert_summary(
        diagnostics,
        connection_attempt_count=len(effects),
        connection_success_count=2,
        connection_setup_failure_count=int(setup_retry),
        reconnect_count=1,
        live_emitted_count=1,
    )


def test_successful_irc_frame_capture_is_bounded_across_reconnects(
    monkeypatch: pytest.MonkeyPatch, captured_frames
) -> None:
    first_frames = [_privmsg(str(index), f"message {index}") for index in range(2)]
    second_frames = [_privmsg(str(index), f"message {index}") for index in range(2, 5)]
    ircs = [
        _FakeIRC(["\r\n".join(first_frames) + "\r\nPING :tmi.twitch.tv\r", ""]),
        _FakeIRC(["\n" + "\r\n".join(second_frames) + "\r\n", ""]),
    ]
    downloader = _downloader(badge_cache=SimpleNamespace(snapshot=lambda: None))
    diagnostics = live_service._TwitchLiveDiagnostics()
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_FRAMES", "yes")

    messages = _live(
        downloader,
        _request(max_attempts=2),
        irc_factory=_irc_factory(*ircs),
        diagnostics=diagnostics,
    )
    received = [next(messages) for _ in range(5)]
    messages.close()

    assert [message["message_id"] for message in received] == [
        str(index) for index in range(5)
    ]
    assert captured_frames == [
        (("twitch-irc-frame", {"raw": f"{frame}\r\n"}), {"sample_limit": 3})
        for frame in [*first_frames, second_frames[0]]
    ]
    assert all(irc.closed for irc in ircs)
    assert all(irc.sent == [] for irc in ircs)
    _assert_summary(
        diagnostics,
        connection_attempt_count=2,
        connection_success_count=2,
        reconnect_count=1,
        received_irc_chunk_count=2,
        received_irc_frame_count=5,
        parsed_irc_message_count=5,
        keepalive_ping_received_count=0,
        keepalive_pong_sent_count=0,
        live_emitted_count=5,
    )


def test_event_frame_capture_is_diverse_and_bounded_across_reconnects(
    monkeypatch: pytest.MonkeyPatch, captured_frames
) -> None:
    resub_one = _usernotice("resub-1", "resub", "First resub")
    text_message = _privmsg("text-1", "Hello")
    resub_two = _usernotice("resub-2", "resub", "Second resub")
    milestone = _usernotice("milestone-1", "viewermilestone", "Milestone")
    ircs = [
        _FakeIRC([f"{resub_one}\r\n{text_message}\r\n", ""]),
        _FakeIRC([f"{resub_two}\r\n{milestone}\r\n", ""]),
    ]
    downloader = _downloader(badge_cache=SimpleNamespace(snapshot=lambda: None))
    diagnostics = live_service._TwitchLiveDiagnostics()
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES", "on")

    messages = _live(
        downloader,
        _request(max_attempts=2, message_groups=["all"]),
        irc_factory=_irc_factory(*ircs),
        diagnostics=diagnostics,
    )
    received = [next(messages) for _ in range(4)]
    messages.close()

    assert [message["message_type"] for message in received] == [
        "resubscription",
        "text_message",
        "resubscription",
        "viewermilestone",
    ]
    assert captured_frames == [
        (
            (f"twitch-irc-event-message-{label}", {"raw": f"{frame}\r\n"}),
            {
                "sample_limit": 1,
                "sample_group": "twitch-irc-event-frames",
                "group_limit": 12,
            },
        )
        for label, frame in [
            ("resubscription-7dce7b9831c9", resub_one),
            ("text-message-18e44952e1aa", text_message),
            ("viewermilestone-71b63634a922", milestone),
        ]
    ]
    assert all(irc.closed for irc in ircs)
    _assert_summary(diagnostics, reconnect_count=1, parsed_irc_message_count=4)


@pytest.mark.parametrize(
    ("proxy", "receive_timeout"),
    [("socks5h://proxy.test:1080", 1.0), (None, 0.1)],
    ids=["effective-proxy", "clamped-timeout"],
)
def test_live_connection_configuration(proxy, receive_timeout):
    irc = Mock()
    factory = _irc_factory(irc)
    downloader = _downloader(
        session=SimpleNamespace(
            proxies={"https": proxy} if proxy else {}, trust_env=False
        )
    )
    assert (
        _run_live(
            downloader,
            _request(message_receive_timeout=receive_timeout),
            irc_factory=factory,
        )
        == []
    )
    factory.assert_called_once_with(connect_timeout=10.0, proxy_url=proxy)
    irc.set_timeout.assert_called_once_with(1.0)


@pytest.mark.parametrize(
    ("failure", "error"),
    [("setup", RuntimeError), ("disconnect", RetriesExceeded), ("join", None)],
)
def test_live_connection_failures_close_connections(failure, error):
    ircs = [Mock(), Mock()]
    effects = ircs
    generator = _message_generator()
    if failure == "setup":
        effects = [OSError("connection refused")]
    elif failure == "disconnect":
        generator = _message_generator(ConnectionError("one"), ConnectionError("two"))
    else:
        ircs[0].join_channel.side_effect = OSError("join failed")
    with pytest.raises(error) if error else nullcontext():
        assert (
            _run_live(
                request=_request(max_attempts=len(effects)),
                irc_factory=_irc_factory(*effects),
                message_generator=generator,
            )
            == []
        )
    if failure != "setup":
        assert all(irc.close_connection.called for irc in ircs)


def test_live_service_default_messages_include_social_sharing_badge() -> None:
    social_sharing_badge = {
        "action_type": "user_notice",
        "message_type": "social_sharing_badge",
        "message_id": "social-badge-1",
    }

    result = _run_live(
        _downloader(),
        _request(),
        message_generator=_message_generator([social_sharing_badge]),
    )

    assert result == [social_sharing_badge]


def test_live_service_iter_stream_chat_messages_filters_and_logs_every_250th() -> None:
    request = _request(max_attempts=2)
    messages = [
        {"message_type": "text_message", "message_id": str(index)}
        for index in range(251)
    ]
    fake_filter = Mock()
    fake_filter.should_add.side_effect = [False] + [True] * 250

    with (
        patch.object(live_service, "log") as mock_log,
        patch.object(
            live_service.MessageFilter, "from_request", return_value=fake_filter
        ),
    ):
        result = _run_live(
            _downloader(), request, message_generator=_message_generator(messages)
        )

    assert len(result) == 250
    mock_log.assert_any_call("debug", "Total number of messages: 250")


def test_live_service_iter_stream_chat_messages_deduplicates_by_message_id() -> None:
    messages = [
        {"message_type": "text_message", "message_id": "dup", "message": "first"},
        {"message_type": "text_message", "message_id": "dup", "message": "duplicate"},
        {"message_type": "text_message", "message_id": "unique", "message": "second"},
    ]
    diagnostics = live_service._TwitchLiveDiagnostics()

    result = _run_live(
        _downloader(),
        _request(),
        message_generator=_message_generator(messages),
        diagnostics=diagnostics,
    )

    assert result == [messages[0], messages[2]]
    _assert_summary(
        diagnostics, duplicate_message_suppressed_count=1, live_emitted_count=2
    )


def test_live_service_iter_stream_chat_messages_rejects_zero_attempts() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        _request(max_attempts=0)


@pytest.mark.parametrize(
    ("degraded", "retry_first"),
    [(False, False), (True, False), (True, True)],
    ids=["clean", "degraded", "degraded-retry"],
)
def test_real_live_get_chat_reports_content_free_metadata_diagnostics(
    degraded,
    retry_first,
) -> None:
    errors = [{"message": "service error", "path": ["user", "primaryTeam"]}]
    if not degraded:
        errors = None
    payloads = [_stream_metadata_payload(errors=errors)]
    if retry_first:
        payloads.insert(0, [{"data": {}, "errors": errors}])
    session_post = Mock(
        side_effect=[
            Mock(status_code=200, text="", json=Mock(return_value=payload))
            for payload in payloads
        ]
    )
    downloader = TwitchChatDownloader()
    downloader._session_post = session_post
    downloader._update_badge_info = Mock()

    chat = downloader.get_chat_by_stream_id(
        "northernlion",
        _request(url="https://www.twitch.tv/northernlion", max_attempts=len(payloads)),
    )

    assert session_post.call_count == len(payloads)
    assert chat.diagnostics["optional_metadata_degradation_count"] == (
        len(payloads) if degraded else 0
    )
    assert all(isinstance(value, int) for value in chat.diagnostics.values())
    rendered_diagnostics = repr(chat.diagnostics).casefold()
    for private_value in ("primaryteam", "service error", "northernlion"):
        assert private_value not in rendered_diagnostics
    chat.close()
    downloader.close()


@pytest.mark.parametrize(
    ("channel", "user", "failure", "title", "status", "warning"),
    [
        (
            "example",
            {
                "id": "channel-123",
                "stream": {"type": "rerun"},
                "lastBroadcast": {"title": "Rerun Title"},
            },
            None,
            "Rerun Title",
            "live",
            "broadcasting a rerun",
        ),
        (
            "offline-channel",
            {"stream": {"type": None}, "lastBroadcast": {"title": "Ignored"}},
            None,
            "offline-channel",
            "upcoming",
            "not currently live",
        ),
        (
            "example",
            {"stream": {"type": "live"}, "lastBroadcast": {"title": "Live Title"}},
            [{}],
            "Live Title",
            "live",
            None,
        ),
        ("missing-channel", None, RequestException("temporary"), None, None, None),
    ],
    ids=["rerun", "offline", "schema-retry", "missing-user-retry"],
)
def test_live_metadata(caplog, channel, user, failure, title, status, warning):
    responses = [[{"data": {"user": user}}]]
    if failure is not None:
        responses.insert(0, failure)
    downloader = _stream_downloader(*responses)
    request = _request(
        url=f"https://www.twitch.tv/{channel}", max_attempts=len(responses)
    )
    if user is None:
        with pytest.raises(live_service.UserNotFound):
            live_service.get_chat_by_stream_id(
                cast("Any", downloader), channel, request
            )
        downloader._update_badge_info.assert_not_called()
    else:
        with caplog.at_level(logging.INFO, logger="chat_downloader"):
            chat = live_service.get_chat_by_stream_id(
                cast("Any", downloader), channel, request
            )
        assert (chat.title, chat.status) == (title, status)
        if warning:
            assert any(warning in record.message for record in caplog.records)
        if "id" in user:
            assert chat.diagnostics["connection_attempt_count"] == 0
            downloader._update_badge_info.assert_called_once_with(channel, user["id"])
    if failure is not None:
        downloader.retry.assert_called_once()


@pytest.mark.parametrize(
    ("message_ids", "duplicates"),
    [
        ([None, ""], [False, False]),
        (["old", "old", "middle", "new", "old"], [False, True, False, False, False]),
    ],
    ids=["invalid-ids", "oldest-eviction"],
)
def test_duplicate_live_message_cache(message_ids, duplicates):
    cache = live_service._SeenMessageCache(limit=2)
    assert [
        live_service._is_duplicate_live_message(message_id, cache)
        for message_id in message_ids
    ] == duplicates
    if message_ids[0] is None:
        assert list(cache.message_ids) == []
