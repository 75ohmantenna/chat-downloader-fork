# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import Any, cast
from unittest.mock import Mock, patch

import pytest

from chat_downloader.errors import (
    CaptchaChallengeRequired,
    LoginRequired,
    ParsingError,
    VideoNotFound,
    VideoUnavailable,
    VideoUnplayable,
)
from chat_downloader.models import ChatRequest
from chat_downloader.sites.twitch import graphql_client, irc_diagnostics, irc_transport


def _privmsg(message_id: str, text: str) -> str:
    return (
        "@badge-info=;badges=;color=;display-name=User;emotes=;id="
        f"{message_id};mod=0;room-id=1;subscriber=0;tmi-sent-ts=1;turbo=0;user-id=1;"
        f"user-type= :user!user@user.tmi.twitch.tv PRIVMSG #example :{text}"
    )


def test_successful_irc_frame_capture_requires_explicit_scope_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_FRAMES",
        raising=False,
    )
    captured = []
    monkeypatch.setattr(
        irc_diagnostics,
        "capture_debug_sample",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )

    irc_diagnostics._SuccessfulIrcFrameCapture().capture("valid frame\r\n")

    assert captured == []


@pytest.mark.parametrize(
    ("message", "expected_exception"),
    [
        ("resource not found", VideoNotFound),
        ("not authorized to view this resource", LoginRequired),
        ("subscription required for this video", VideoUnplayable),
        ("this content was deleted", VideoUnavailable),
    ],
)
def test_handle_gql_errors_maps_known_messages(message, expected_exception) -> None:
    with pytest.raises(expected_exception):
        graphql_client._handle_gql_errors([{"message": message, "path": ["root"]}])


def test_handle_gql_errors_ignores_malformed_error_item() -> None:
    graphql_client._handle_gql_errors(["not-a-dict"])


def test_handle_gql_errors_raises_parsing_error_with_path() -> None:
    with pytest.raises(ParsingError) as excinfo:
        graphql_client._handle_gql_errors(
            [
                {
                    "message": "unexpected failure",
                    "path": ["video", "comments", 0],
                }
            ],
            ["VideoCommentsByOffsetOrCursor"],
        )

    assert "video -> comments -> 0" in str(excinfo.value)
    assert "VideoCommentsByOffsetOrCursor" in str(excinfo.value)


@pytest.mark.parametrize(
    "message",
    ["PersistedQueryNotFound", "Persisted query not found"],
)
def test_handle_gql_errors_reports_persisted_query_failures_actionably(
    message: str,
) -> None:
    with pytest.raises(graphql_client._PersistedQueryUnavailable) as excinfo:
        graphql_client._handle_gql_errors(
            [{"message": message, "path": ["video"]}],
            ["StreamMetadata"],
        )

    message = str(excinfo.value)
    assert "StreamMetadata" in message
    assert "Operation hashes or required variables may be stale" in message


def test_download_gql_handles_dict_error_response() -> None:
    def session_post(_url, json, headers):
        assert headers["Client-ID"]
        assert json[0]["extensions"]["persistedQuery"]["version"] == 1
        return type(
            "_Resp",
            (),
            {
                "json": staticmethod(
                    lambda: {"errors": [{"message": "resource not found"}]},
                ),
            },
        )()

    with pytest.raises(VideoNotFound):
        graphql_client._download_gql(
            session_post,
            [
                {
                    "operationName": next(iter(graphql_client.OPERATION_HASHES)),
                    "variables": {},
                },
            ],
        )


def test_download_gql_handles_list_error_response() -> None:
    def session_post(_url, json, headers):
        assert headers["Client-ID"]
        assert json[0]["extensions"]["persistedQuery"]["version"] == 1
        return type(
            "_Resp",
            (),
            {
                "json": staticmethod(
                    lambda: [{"errors": [{"message": "resource not found"}]}],
                ),
            },
        )()

    with pytest.raises(VideoNotFound):
        graphql_client._download_gql(
            session_post,
            [
                {
                    "operationName": next(iter(graphql_client.OPERATION_HASHES)),
                    "variables": {},
                },
            ],
        )


def test_download_base_gql_raises_captcha_challenge_required_on_challenge_response() -> (  # noqa: E501
    None
):
    def session_post(_url, json, headers):
        del json, headers
        return type(
            "_Resp",
            (),
            {
                "status_code": 403,
                "text": "Kasada challenge required",
                "json": staticmethod(dict),
            },
        )()

    with pytest.raises(CaptchaChallengeRequired) as exc_info:
        graphql_client._download_base_gql(session_post, [{"operationName": "x"}])

    assert "twitch_web" in str(exc_info.value)


def test_download_gql_rejects_missing_hash_mapping() -> None:
    from chat_downloader.metadata import __version__

    with pytest.raises(ParsingError) as excinfo:
        graphql_client._download_gql(
            Mock(),
            [{"operationName": "NonexistentOperation", "variables": {}}],
        )

    message = str(excinfo.value)
    assert "Missing Twitch persisted GraphQL hash mapping" in message
    # Actionable diagnostics: name the bad op, the package version, the
    # exact file to patch, and how to find the new hash.
    assert "NonexistentOperation" in message
    assert __version__ in message
    assert "src/chat_downloader/sites/twitch/constants.py" in message
    assert "persistedQuery" in message


def test_twitch_chat_irc_join_channel_is_idempotent() -> None:
    irc = irc_transport.TwitchChatIRC.__new__(irc_transport.TwitchChatIRC)
    irc.current_channel = None
    irc_any = cast("Any", irc)
    irc_any.send_raw = Mock()

    irc_any.join_channel("Example")
    irc_any.join_channel("example")
    irc_any.join_channel("Other")

    assert irc_any.send_raw.call_args_list == [
        (("JOIN #example",), {}),
        (("JOIN #other",), {}),
    ]


def test_twitch_chat_irc_timeout_and_close_delegate_to_socket() -> None:
    irc = irc_transport.TwitchChatIRC.__new__(irc_transport.TwitchChatIRC)
    irc.socket = Mock()

    irc.set_timeout(1.25)
    irc.close_connection()

    irc.socket.settimeout.assert_called_once_with(1.25)
    irc.socket.shutdown.assert_called_once_with(irc_transport.socket.SHUT_WR)
    irc.socket.close.assert_called_once_with()


def test_twitch_chat_irc_constructor_send_raw_and_recv(monkeypatch) -> None:
    fake_socket = Mock()
    fake_socket.recv.return_value = b"hello world"

    monkeypatch.setattr(
        irc_transport,
        "open_proxied_tls_socket",
        lambda *args, **kwargs: fake_socket,
    )

    irc = irc_transport.TwitchChatIRC()

    assert fake_socket.settimeout.call_args_list == [((None,), {})]
    assert fake_socket.sendall.call_args_list == [
        (
            (b"CAP REQ :twitch.tv/tags twitch.tv/commands twitch.tv/membership\r\n",),
            {},
        ),
        ((b"PASS SCHMOOPIIE\r\n",), {}),
        ((b"NICK justinfan67420\r\n",), {}),
    ]

    irc.send_raw("PING")
    assert fake_socket.sendall.call_args_list[-1] == ((b"PING\r\n",), {})
    assert irc.recv(32) == "hello world"


def test_twitch_chat_irc_preserves_utf8_split_across_recv_chunks(
    monkeypatch,
) -> None:
    fake_socket = Mock()
    encoded = "hello 😀 world".encode()
    fake_socket.recv.side_effect = [encoded[:8], encoded[8:]]
    monkeypatch.setattr(
        irc_transport,
        "open_proxied_tls_socket",
        lambda *_args, **_kwargs: fake_socket,
    )
    irc = irc_transport.TwitchChatIRC()

    received = irc.recv(1024) + irc.recv(1024)

    assert received == "hello 😀 world"


@pytest.mark.parametrize(
    ("readbuffer", "expected"),
    [
        ("", True),
        ("PING :tmi.twitch.tv\r\nPONG :tmi.twitch.tv\r\n", True),
        (":user!user@user.tmi.twitch.tv JOIN #example\r\n", True),
        (":user!user@user.tmi.twitch.tv PART #example\r\n", True),
        (":tmi.twitch.tv 001 justinfan :Welcome\r\n", True),
        (
            ":tmi.twitch.tv CAP * ACK :twitch.tv/tags twitch.tv/commands twitch.tv/membership\r\n",  # noqa: E501
            True,
        ),
        (":tmi.twitch.tv NOTICE * :hello\r\n", False),
        ("THIS IS NOT A TWITCH IRC HOUSEKEEPING LINE\r\n", False),
        (":notwitch.example PRIVMSG #example :hello\r\n", False),
    ],
)
def test_is_benign_unmatched_irc_buffer_classifies_expected_lines(
    readbuffer, expected
) -> None:
    assert irc_transport._is_benign_unmatched_irc_buffer(readbuffer) is expected


def test_should_send_keepalive_respects_interval() -> None:
    assert irc_transport._should_send_keepalive(100.1, 40.0, 60.0)
    assert not irc_transport._should_send_keepalive(99.9, 40.0, 60.0)
    assert not irc_transport._should_send_keepalive(100.0, 40.0, 60.0)


def test_maybe_send_keepalive_updates_last_ping_only_when_due() -> None:
    class FakeIRC:
        def __init__(self) -> None:
            self.sent = []

        def send_raw(self, message: str) -> None:
            self.sent.append(message)

    irc = FakeIRC()

    assert (
        irc_transport._maybe_send_keepalive(
            irc,
            current_time=120.0,
            last_ping_time=40.0,
            ping_every=60.0,
        )
        == 120.0
    )
    assert irc.sent == ["PING"]

    assert (
        irc_transport._maybe_send_keepalive(
            irc,
            current_time=150.0,
            last_ping_time=120.0,
            ping_every=60.0,
        )
        == 120.0
    )
    assert irc.sent == ["PING"]


def test_process_irc_buffer_keeps_partial_tail_without_final_newline() -> None:
    full_line = _privmsg("1", "one")
    partial = (
        "@badge-info=;badges=;color=;display-name=User;emotes=;id=2;mod=0;room-id=1;"
        "subscriber=0;tmi-sent-ts=1;turbo=0;user-id=1;user-type= :user!user@user.tmi.twitch.tv PRIVMSG #example :par"  # noqa: E501
    )
    readbuffer_tail, matches = irc_transport._process_irc_buffer(
        f"{full_line}\r\n{partial}",
        irc_transport.MESSAGE_REGEX,
    )

    assert readbuffer_tail == partial
    assert len(matches) == 1
    assert matches[0].group(3) == "one"


def test_consume_irc_buffer_returns_unmatched_full_buffer_only_for_complete_lines() -> (
    None
):
    remaining, matches, unmatched_full_buffer = irc_transport._consume_irc_buffer(
        "UNKNOWN LINE\r\n",
        irc_transport.MESSAGE_REGEX,
    )

    assert remaining == ""
    assert matches == []
    assert unmatched_full_buffer == "UNKNOWN LINE\r\n"


def test_parse_irc_matches_returns_items_and_updated_count(monkeypatch) -> None:
    payload = _privmsg("1", "one") + "\r\n" + _privmsg("2", "two") + "\r\n"
    matches = list(irc_transport.MESSAGE_REGEX.finditer(payload))

    monkeypatch.setattr(
        irc_transport,
        "_parse_irc_item",
        lambda match, _badge_set: {"message": match.group(3)},
    )

    items, message_count = irc_transport._parse_irc_matches(matches, None, 249)

    assert items == [{"message": "one"}, {"message": "two"}]
    assert message_count == 251


def test_irc_transport_sends_pong_on_ping_before_connection_error() -> None:
    class FakeIRC:
        def __init__(self) -> None:
            self.responses = iter(["PING :tmi.twitch.tv\r\n", ""])
            self.sent: list[str] = []

        def recv(self, _buffer_size: int) -> str:
            return next(self.responses)

        def send_raw(self, message: str) -> None:
            self.sent.append(message)

    irc = FakeIRC()

    with pytest.raises(ConnectionError):
        list(
            irc_transport.get_chat_messages_by_stream_id(
                cast("Any", irc),
                "example",
                ChatRequest(url="https://www.twitch.tv/example"),
            ),
        )

    assert irc.sent == [irc_transport.PONG_TEXT]


def test_irc_transport_logs_unknown_full_buffer_when_no_matches() -> None:
    class FakeIRC:
        def __init__(self) -> None:
            self.responses = iter(["UNKNOWN LINE\r\n", ""])

        def recv(self, _buffer_size: int) -> str:
            return next(self.responses)

        def send_raw(self, _message: str) -> None:
            return None

    irc = FakeIRC()

    with (
        patch.object(
            irc_transport,
            "_is_benign_unmatched_irc_buffer",
            return_value=False,
        ),
        patch.object(irc_transport, "log") as mock_log,
        patch.object(
            irc_transport,
            "capture_debug_sample",
        ) as mock_capture_debug_sample,
        pytest.raises(ConnectionError),
    ):
        list(
            irc_transport.get_chat_messages_by_stream_id(
                cast("Any", irc),
                "example",
                ChatRequest(url="https://www.twitch.tv/example"),
            ),
        )

    mock_log.assert_any_call("debug", 'No matches found in "\nUNKNOWN LINE\n"')
    mock_capture_debug_sample.assert_called_once_with(
        "twitch-unknown-irc-shape",
        {"raw": "UNKNOWN LINE\r\n"},
        sample_limit=10,
    )


def test_irc_transport_handles_partial_matches_logs_progress_and_sends_keepalive() -> (
    None
):
    class FakeIRC:
        def __init__(self) -> None:
            self.responses = iter(
                [
                    _privmsg("1", "hello") + "\r\n" + _privmsg("2", "part"),
                    "ial\r\n",
                    "",
                ],
            )
            self.sent: list[str] = []

        def recv(self, _buffer_size: int) -> str:
            response: Any = next(self.responses)
            if isinstance(response, Exception):
                raise response
            return cast("str", response)

        def send_raw(self, message: str) -> None:
            self.sent.append(message)

    irc = FakeIRC()
    time_values = iter([0.0, 61.0, 62.0])

    with (
        patch.object(
            irc_transport.time,
            "monotonic",
            side_effect=lambda: next(time_values),
        ),
        patch.object(
            irc_transport,
            "_parse_irc_item",
            side_effect=[{"message": "hello"}, {"message": "partial"}],
        ) as mock_parse,
        patch.object(irc_transport, "log") as mock_log,
        pytest.raises(ConnectionError),
    ):
        assert list(
            irc_transport.get_chat_messages_by_stream_id(
                cast("Any", irc),
                "example",
                ChatRequest(url="https://www.twitch.tv/example"),
            ),
        ) == [{"message": "hello"}, {"message": "partial"}]

    assert mock_parse.call_count == 2
    mock_log.assert_not_called()
    assert irc.sent == ["PING"]


def test_irc_transport_does_not_log_progress_every_250_messages() -> None:
    class FakeIRC:
        def __init__(self) -> None:
            payload = "\r\n".join(
                _privmsg(str(index), f"message-{index}") for index in range(1, 251)
            )
            self.responses = iter([payload + "\r\n", ""])

        def recv(self, _buffer_size: int) -> str:
            response: Any = next(self.responses)
            return cast("str", response)

        def send_raw(self, _message: str) -> None:
            return None

    parsed_messages = [{"message": f"message-{index}"} for index in range(1, 251)]

    with (
        patch.object(irc_transport, "_parse_irc_item", side_effect=parsed_messages),
        patch.object(irc_transport, "log") as mock_log,
        pytest.raises(ConnectionError),
    ):
        assert (
            len(
                list(
                    irc_transport.get_chat_messages_by_stream_id(
                        cast("Any", FakeIRC()),
                        "example",
                        ChatRequest(url="https://www.twitch.tv/example"),
                    ),
                ),
            )
            == 250
        )

    mock_log.assert_not_called()


def test_irc_transport_preserves_trailing_unmatched_buffer_after_complete_match() -> (
    None
):
    class FakeIRC:
        def __init__(self) -> None:
            self.responses = iter([_privmsg("1", "hello") + "\r\nUNKNOWN", ""])

        def recv(self, _buffer_size: int) -> str:
            response: Any = next(self.responses)
            return cast("str", response)

        def send_raw(self, _message: str) -> None:
            return None

    with (
        patch.object(
            irc_transport,
            "_parse_irc_item",
            return_value={"message": "hello"},
        ),
        pytest.raises(ConnectionError),
    ):
        assert list(
            irc_transport.get_chat_messages_by_stream_id(
                cast("Any", FakeIRC()),
                "example",
                ChatRequest(url="https://www.twitch.tv/example"),
            ),
        ) == [{"message": "hello"}]


def test_irc_transport_swallows_timeout_and_continues_until_disconnect() -> None:
    class FakeIRC:
        def __init__(self) -> None:
            self.responses = iter([TimeoutError("timed out"), ""])

        def recv(self, _buffer_size: int) -> str:
            response: Any = next(self.responses)
            if isinstance(response, Exception):
                raise response
            return cast("str", response)

        def send_raw(self, _message: str) -> None:
            return None

    with pytest.raises(ConnectionError):
        list(
            irc_transport.get_chat_messages_by_stream_id(
                cast("Any", FakeIRC()),
                "example",
                ChatRequest(url="https://www.twitch.tv/example"),
            ),
        )


def test_irc_transport_maps_socket_receive_error_to_reconnect() -> None:
    class FakeIRC:
        def recv(self, _buffer_size: int) -> str:
            raise OSError("network changed")

    with pytest.raises(ConnectionError, match="receive failed"):
        next(
            irc_transport.get_chat_messages_by_stream_id(
                cast("Any", FakeIRC()),
                "example",
                ChatRequest(url="https://www.twitch.tv/example"),
            )
        )


def test_irc_transport_idle_watchdog_sends_keepalive_then_reconnects() -> None:
    class FakeIRC:
        def __init__(self) -> None:
            self.sent: list[str] = []

        def recv(self, _buffer_size: int) -> str:
            raise TimeoutError

        def send_raw(self, message: str) -> None:
            self.sent.append(message)

    irc = FakeIRC()
    time_values = iter([0.0, 61.0, 180.0])

    with (
        patch.object(
            irc_transport.time,
            "monotonic",
            side_effect=lambda: next(time_values),
        ),
        patch.object(irc_transport, "log") as mock_log,
        pytest.raises(ConnectionError, match="became idle"),
    ):
        next(
            irc_transport.get_chat_messages_by_stream_id(
                cast("Any", irc),
                "example",
                ChatRequest(url="https://www.twitch.tv/example"),
            )
        )

    assert irc.sent == ["PING", "PING"]
    mock_log.assert_called_once_with(
        "debug",
        "Twitch IRC idle watchdog expired after 180s; reconnecting.",
    )


def test_irc_transport_pong_oserror_raises_connection_error() -> None:
    """OSError from send_raw(PONG) becomes ConnectionError for reconnect."""

    class FakeIRC:
        def __init__(self) -> None:
            self.responses = iter(["PING :tmi.twitch.tv\r\n"])

        def recv(self, _buffer_size: int) -> str:
            return next(self.responses)

        def send_raw(self, _message: str) -> None:
            raise OSError("broken pipe")

    with pytest.raises(ConnectionError):
        list(
            irc_transport.get_chat_messages_by_stream_id(
                cast("Any", FakeIRC()),
                "example",
                ChatRequest(url="https://www.twitch.tv/example"),
            ),
        )


def test_irc_transport_ping_oserror_raises_connection_error() -> None:
    """OSError from send_raw(PING) becomes ConnectionError for reconnect."""

    class FakeIRC:
        def __init__(self) -> None:
            # First recv returns a full buffer with no IRC matches (benign
            # line); the ping check then runs and send_raw("PING") raises
            # OSError.
            self.responses = iter(["UNKNOWN LINE\r\n"])

        def recv(self, _buffer_size: int) -> str:
            try:
                return next(self.responses)
            except StopIteration:
                raise TimeoutError from None

        def send_raw(self, _message: str) -> None:
            raise OSError("broken pipe")

    # monotonic() called at init (→0.0), then after recv succeeds (→61.0),
    # triggering the PING send_raw which raises OSError → ConnectionError.
    time_values = iter([0.0, 61.0])

    with (
        patch.object(
            irc_transport.time,
            "monotonic",
            side_effect=lambda: next(time_values),
        ),
        pytest.raises(ConnectionError),
    ):
        list(
            irc_transport.get_chat_messages_by_stream_id(
                cast("Any", FakeIRC()),
                "example",
                ChatRequest(url="https://www.twitch.tv/example"),
            ),
        )


def test_twitch_chat_irc_constructor_closes_socket_on_send_raw_oserror(
    monkeypatch,
) -> None:
    """SSL socket must be closed if send_raw raises during IRC registration."""
    fake_socket = Mock()
    fake_socket.sendall.side_effect = OSError("broken pipe")

    monkeypatch.setattr(
        irc_transport,
        "open_proxied_tls_socket",
        lambda *args, **kwargs: fake_socket,
    )

    with pytest.raises(OSError):
        irc_transport.TwitchChatIRC()

    fake_socket.close.assert_called_once()


def test_twitch_chat_irc_close_connection_sends_quit_before_closing(
    monkeypatch,
) -> None:
    """close_connection() sends QUIT and shutdown before closing the socket."""
    irc = irc_transport.TwitchChatIRC.__new__(irc_transport.TwitchChatIRC)
    irc.socket = Mock()
    irc.socket.shutdown = Mock()
    sent: list[str] = []
    irc.send_raw = sent.append

    irc.close_connection()

    assert "QUIT" in sent
    irc.socket.shutdown.assert_called_once_with(irc_transport.socket.SHUT_WR)
    irc.socket.close.assert_called_once()

    irc.close_connection()
    irc.socket.close.assert_called_once()


def test_download_base_gql_raises_http_error_for_non_captcha_4xx() -> None:
    """Non-captcha 4xx/5xx must raise HTTPError via raise_for_status()."""
    from requests.exceptions import HTTPError

    mock_response = Mock()
    mock_response.status_code = 429
    mock_response.text = "Too Many Requests"
    mock_response.raise_for_status.side_effect = HTTPError("429")

    with pytest.raises(HTTPError):
        graphql_client._download_base_gql(
            lambda *args, **kwargs: mock_response,
            [{"operationName": "x"}],
        )

    mock_response.raise_for_status.assert_called_once()


def test_update_badge_info_skips_malformed_badge_and_keeps_others() -> None:
    """One malformed badge ID must not drop the channel's other badges."""
    import base64

    def make_badge_id(set_id: str, version: str, channel_id: str) -> str:
        return base64.b64encode(f"{set_id};{version};{channel_id}".encode()).decode()

    good_badge = {
        "id": make_badge_id("subscriber", "6", ""),
        "title": "6-Month Sub",
    }
    bad_badge = {
        "id": base64.b64encode(b"notvalid").decode(),
        "title": "Bad Badge",
    }

    def fake_download_gql(_session_post, query, client_id=None):
        op = query[0]["operationName"]
        if op == "ChatList_Badges":
            return [{"data": {"badges": [good_badge, bad_badge], "user": None}}]
        return [{"data": {"badges": [], "user": None}}]

    badge_info: dict = {}
    subscriber_badge_info: dict = {}

    graphql_client.update_badge_info(
        session_post=Mock(),
        channel="example",
        download_gql_func=fake_download_gql,
        badge_info=badge_info,
        subscriber_badge_info=subscriber_badge_info,
    )

    # Good badge should be stored; bad badge silently skipped.
    assert ("subscriber", "6") in badge_info
    # Only one entry — bad badge dropped without killing the batch.
    assert len(badge_info) == 1
