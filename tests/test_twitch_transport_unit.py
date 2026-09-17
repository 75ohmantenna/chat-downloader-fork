# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest
from requests.exceptions import HTTPError

import chat_downloader.redaction as red
from chat_downloader.errors import (
    CaptchaChallengeRequired,
    LoginRequired,
    ParsingError,
    VideoNotFound,
    VideoUnavailable,
    VideoUnplayable,
)
from chat_downloader.models import ChatRequest
from chat_downloader.sites.twitch import (
    badge_client,
    graphql_client,
    irc_diagnostics,
    irc_transport,
)
from tests.twitch_third_helpers import irc_frame


def _privmsg(message_id: str, text: str) -> str:
    return irc_frame(
        f"id={message_id};display-name=User;room-id=1;user-id=1",
        text=text,
        channel="example",
        user="user",
    ).removesuffix("\r\n")


def _usernotice(message_id: str, message_type: str, text: str) -> str:
    return irc_frame(
        f"id={message_id};msg-id={message_type};display-name=User;room-id=1;"
        "subscriber=1;user-id=1;system-msg=Event",
        "USERNOTICE",
        text,
        "example",
        "",
    ).removesuffix("\r\n")


def _capture_resub(capture, frame):
    _capture_message(capture, frame, "resubscription", "resub")


def _capture_message(capture, frame, message_type, raw_type=""):
    tags = f"msg-id={raw_type}" if raw_type else ""
    capture.capture(frame, {"message_type": message_type}, "USERNOTICE", tags)


def _capture_key(message_type, action="USERNOTICE", tags="msg-id=resub"):
    return irc_diagnostics._event_capture_key(
        {"message_type": message_type}, action, tags
    )


def _parse_frames(frames):
    return irc_transport._parse_irc_matches(
        list(irc_transport.MESSAGE_REGEX.finditer("\r\n".join(frames) + "\r\n")),
        None,
        0,
        event_frame_capture=irc_diagnostics._EventDiverseIrcFrameCapture(),
    )[0]


@pytest.fixture
def captured_frames(monkeypatch):
    captured = []

    def record_capture(*args, **kwargs):
        captured.append((args, kwargs))
        return f"/samples/{len(captured)}.json"

    monkeypatch.setattr(irc_diagnostics, "capture_debug_sample", record_capture)
    return captured


@pytest.fixture
def event_capture(monkeypatch):
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES", "1")
    return irc_diagnostics._EventDiverseIrcFrameCapture()


@pytest.fixture
def sample_dir(monkeypatch, tmp_path, event_capture):
    directory = tmp_path / "samples"
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(directory))
    monkeypatch.setattr(red, "_debug_sample_capture_enabled", lambda: True)
    return directory


@pytest.mark.parametrize("event_mode", [False, True], ids=["first", "event"])
def test_frame_capture_requires_explicit_scope_opt_in(
    monkeypatch,
    captured_frames,
    event_mode,
) -> None:
    scope = "TWITCH_IRC_EVENT_FRAMES" if event_mode else "TWITCH_IRC_FRAMES"
    monkeypatch.delenv(f"CHAT_DOWNLOADER_CAPTURE_{scope}", raising=False)
    if event_mode:
        _capture_resub(
            irc_diagnostics._EventDiverseIrcFrameCapture(), "valid frame\r\n"
        )
    else:
        irc_diagnostics._SuccessfulIrcFrameCapture().capture("valid frame\r\n")

    assert captured_frames == []


def test_event_frame_capture_prefers_message_type_and_falls_back_to_action(
    monkeypatch: pytest.MonkeyPatch,
    captured_frames,
) -> None:
    captured = captured_frames
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES", "yes")
    frame_capture = irc_diagnostics._EventDiverseIrcFrameCapture()

    for frame in ("resub one\r\n", "resub two\r\n"):
        _capture_resub(frame_capture, frame)
    _capture_message(
        frame_capture, "milestone\r\n", "viewermilestone", "viewermilestone"
    )
    frame_capture.capture("notice\r\n", {}, "NOTICE", "")

    assert [(args[0].rsplit("-", 1)[0], args[1]) for args, _ in captured] == [
        (f"twitch-irc-event-{label}", {"raw": frame})
        for label, frame in [
            ("message-resubscription", "resub one\r\n"),
            ("message-viewermilestone", "milestone\r\n"),
            ("action-notice", "notice\r\n"),
        ]
    ]
    assert all(
        kwargs
        == {
            "sample_limit": 1,
            "sample_group": "twitch-irc-event-frames",
            "group_limit": 12,
        }
        for _, kwargs in captured
    )


def test_event_frame_capture_bounds_provider_controlled_keys_and_labels(
    event_capture,
    captured_frames,
):
    captured = captured_frames
    frame_capture = event_capture

    for index in range(20):
        attacker_value = f"../Provider\\Type-{index}-" + "x" * 500
        frame_capture.capture(f"frame {index}\r\n", {}, attacker_value, "")

    labels = [args[0] for args, _kwargs in captured]
    assert len(labels) == 12
    assert len(frame_capture._captured_event_keys) == 12
    assert len(frame_capture._event_key_attempts) == 12
    assert all(len(label) <= 72 for label in labels)
    assert all(
        "/" not in label and "\\" not in label and ".." not in label for label in labels
    )


def test_unknown_action_components_are_opaque_stable_and_collision_resistant() -> None:
    provider_values = [
        "foo-bar",
        "foo_bar",
        "A B",
        "a/b",
        "../???",
        "CaseSensitive",
        "casesensitive",
    ]

    components = [
        irc_diagnostics._action_event_component(value) for value in provider_values
    ]

    assert len(components) == len(set(components))
    assert components == [
        irc_diagnostics._action_event_component(value) for value in provider_values
    ]
    assert all(len(component) == 20 for component in components)
    assert all(component.startswith("unknown-") for component in components)
    assert not any(
        fragment in component
        for component in components
        for fragment in ("foo", "case", "authorization", "/", "_")
    )


def test_unknown_action_credentials_share_sanitized_opaque_identity() -> None:
    first = irc_diagnostics._action_event_component(
        "Authorization=BearerFirstSecretCredential123",
    )
    second = irc_diagnostics._action_event_component(
        "Authorization=BearerSecondSecretCredential456",
    )

    assert first == second
    assert first.startswith("unknown-")
    assert "authorization" not in first
    assert "credential" not in first


def test_unknown_action_capture_keeps_credentials_out_of_identity_and_logs(
    monkeypatch,
    sample_dir,
    caplog,
    event_capture,
):
    canary = "SuperSecretCredential123"
    raw_action = f"Authorization=Bearer{canary}"
    raw_frame = f"@room-id=1 :provider.test {raw_action} #example :public message\r\n"
    captured_labels: list[str] = []
    caplog.set_level(logging.DEBUG, logger=red._get_logger().name)

    def capture_real_sample(label, payload, **kwargs):
        captured_labels.append(label)
        return red.capture_debug_sample(label, payload, **kwargs)

    monkeypatch.setattr(
        irc_diagnostics,
        "capture_debug_sample",
        capture_real_sample,
    )
    frame_capture = event_capture
    frame_capture.capture(raw_frame, {}, raw_action, "room-id=1")

    event_key = next(iter(frame_capture._captured_event_keys))
    sample_path = next(sample_dir.glob("*.json"))
    stored_payload = json.loads(sample_path.read_text(encoding="utf-8"))

    assert event_key.startswith("action-unknown-")
    assert captured_labels[0].startswith("twitch-irc-event-action-unknown-")
    for exposed_value in (
        event_key,
        captured_labels[0],
        str(sample_path),
        caplog.text,
    ):
        assert raw_action not in exposed_value
        assert canary not in exposed_value
    assert canary not in stored_payload["raw"]
    assert raw_action not in stored_payload["raw"]
    assert red.REDACTED in stored_payload["raw"]


def test_event_keys_require_recognized_case_sensitive_raw_provenance() -> None:
    keys_by_normalized_type: dict[str, str] = {}
    for raw_msg_id, normalized_type in irc_diagnostics.MESSAGE_TYPE_REMAPPING.items():
        key = _capture_key(
            normalized_type, tags=f"room-id=1;msg-id={raw_msg_id};user-id=2"
        )
        assert key.startswith("message-")
        assert keys_by_normalized_type.setdefault(normalized_type, key) == key

    assert len(set(keys_by_normalized_type.values())) == len(keys_by_normalized_type)
    for raw_action, normalized_type in irc_diagnostics.ACTION_TYPE_REMAPPING.items():
        key = _capture_key(normalized_type, raw_action, "room-id=1;user-id=2")
        assert key.startswith("message-")

    for message_type, action, tags, prefix in [
        ("resubscription", "USERNOTICE", "msg-id=resub", "message-resubscription-"),
        ("resubscription", "USERNOTICE", "msg-id=resubscription", "action-usernotice-"),
        ("resubscription", "USERNOTICE", "msg-id=RESUB", "action-usernotice-"),
        ("text_message", "USERNOTICE", "msg-id=text_message", "action-usernotice-"),
        ("text_message", "text_message", "room-id=1", "action-unknown-"),
        (
            "resubscription",
            "USERNOTICE",
            "msg-id=resub;msg-id=resubscription",
            "action-usernotice-",
        ),
    ]:
        assert _capture_key(message_type, action, tags).startswith(prefix)


@pytest.mark.parametrize("transient", [False, True])
def test_event_capture_bounds_failed_attempts(monkeypatch, event_capture, transient):
    backend = Mock(side_effect=[None, "/samples/success.json"] if transient else None)
    if not transient:
        backend.return_value = None
    monkeypatch.setattr(irc_diagnostics, "capture_debug_sample", backend)
    if transient:
        for index in range(3):
            _capture_resub(event_capture, f"resub {index}\r\n")
        key = _capture_key("resubscription")
        assert backend.call_count == 2
        assert event_capture._event_key_attempts == {key: 2}
        assert event_capture._captured_event_keys == {key}
    else:
        for index in range(20):
            for attempt in range(4):
                event_capture.capture(
                    f"frame {index}-{attempt}\r\n", {}, f"ACTION-{index}", ""
                )
        assert backend.call_count == 24
        assert len(event_capture._event_key_attempts) == 12
        assert set(event_capture._event_key_attempts.values()) == {2}
        assert event_capture._captured_event_keys == set()


def test_event_capture_backend_group_is_shared_by_directory_across_runs(sample_dir):
    known_types = sorted(irc_diagnostics._KNOWN_NORMALIZED_MESSAGE_TYPES)
    first_run_capture = irc_diagnostics._EventDiverseIrcFrameCapture()
    for index, message_type in enumerate(known_types[:12]):
        _capture_message(first_run_capture, f"first run {index}\r\n", message_type)

    later_run_capture = irc_diagnostics._EventDiverseIrcFrameCapture()
    for index in range(3):
        _capture_message(later_run_capture, f"later run {index}\r\n", known_types[12])

    assert len(list(sample_dir.glob("*.json"))) == 12
    assert len(first_run_capture._captured_event_keys) == 12
    assert later_run_capture._captured_event_keys == set()
    assert set(later_run_capture._event_key_attempts.values()) == {2}


def test_event_capture_backend_label_persists_across_runs_with_group_slots(
    monkeypatch,
    sample_dir,
):
    backend_paths = []

    def record_backend_path(*args, **kwargs):
        path = red.capture_debug_sample(*args, **kwargs)
        backend_paths.append(path)
        return path

    monkeypatch.setattr(
        irc_diagnostics,
        "capture_debug_sample",
        record_backend_path,
    )

    for _ in range(2):
        _capture_resub(
            irc_diagnostics._EventDiverseIrcFrameCapture(), "same payload\r\n"
        )

    changed_payload_capture = irc_diagnostics._EventDiverseIrcFrameCapture()
    for index in range(3):
        _capture_resub(changed_payload_capture, f"different payload {index}\r\n")

    _capture_message(
        irc_diagnostics._EventDiverseIrcFrameCapture(),
        "different event\r\n",
        "viewermilestone",
        "viewermilestone",
    )

    assert backend_paths[0] is not None
    assert backend_paths[1] == backend_paths[0]
    assert backend_paths[2:4] == [None, None]
    assert backend_paths[4] is not None
    assert len(list(sample_dir.glob("*.json"))) == 2
    assert changed_payload_capture._captured_event_keys == set()
    assert set(changed_payload_capture._event_key_attempts.values()) == {2}


@pytest.mark.parametrize(
    ("frames", "types", "labels"),
    [
        (
            [
                _usernotice("genuine", "resub", "Genuine resub"),
                _usernotice("masquerade", "resubscription", "Unknown raw type"),
            ],
            ["resubscription"] * 2,
            ["message-resubscription", "action-usernotice"],
        ),
        (
            [
                _privmsg("genuine", "Genuine text"),
                _usernotice("masquerade", "text_message", "Unknown raw type"),
            ],
            ["text_message"] * 2,
            ["message-text-message", "action-usernotice"],
        ),
        (
            [
                _usernotice("unknown-1", "unknown-one", "First unknown"),
                _usernotice("unknown-2", "unknown-two", "Second unknown"),
                irc_frame(action="MYSTERY", user="").removesuffix("\r\n"),
            ],
            ["unknown-one", "unknown-two", "MYSTERY"],
            ["action-usernotice", "action-unknown"],
        ),
    ],
)
def test_real_parser_capture_requires_raw_provenance(
    event_capture, captured_frames, frames, types, labels
):
    assert [item["message_type"] for item in _parse_frames(frames)] == types
    captured_labels = [args[0] for args, _kwargs in captured_frames]
    assert len(captured_labels) == len(labels)
    assert all(
        actual.startswith(f"twitch-irc-event-{expected}-")
        for actual, expected in zip(captured_labels, labels, strict=True)
    )


def test_successful_capture_modes_have_additive_fifteen_frame_limit(
    monkeypatch,
    captured_frames,
    event_capture,
):
    captured = captured_frames
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_FRAMES", "1")
    first_frame_capture = irc_diagnostics._SuccessfulIrcFrameCapture()
    for index in range(20):
        raw_frame = f"frame {index}\r\n"
        first_frame_capture.capture(raw_frame)
        event_capture.capture(raw_frame, {}, f"ACTION-{index}", "")

    assert len(captured) == 15
    assert [args[1]["raw"] for args, _kwargs in captured].count("frame 0\r\n") == 2
    assert sum(args[0] == "twitch-irc-frame" for args, _kwargs in captured) == 3
    assert (
        sum(args[0].startswith("twitch-irc-event-") for args, _kwargs in captured) == 12
    )


@pytest.mark.parametrize(
    ("chunks", "frames", "controls"),
    [
        (
            [
                "PING :tmi.twitch.tv\r",
                "\n:tmi.twitch.tv PONG tmi.twitch.tv :tmi.twitch.tv\r\n",
                "x" * 100 + "PING :tmi.twitch.tv\r\n",
            ],
            3,
            2,
        ),
        (
            [
                (
                    ":tmi.twitch.tv 001 justinfan :Welcome\r\n"
                    ":tmi.twitch.tv CAP * ACK :twitch.tv/tags twitch.tv/commands\r\n"
                    ":user!user@user.tmi.twitch.tv JOIN #example\r\n"
                    "@badge-info=;badges= :user!user@user.tmi.twitch.tv "
                    "PRIVMSG #example :JOIN #another-channel\r\n"
                    "@badge-info= :user!user@user.tmi.twitch.tv JOIN #example\r\n"
                    ":tmi.twitch.tv 421 justinfan CAP :Unknown command\r\n"
                    "UNKNOWN LINE\r\n"
                )
            ],
            7,
            3,
        ),
    ],
)
def test_live_diagnostics_control_frames_and_bounded_state(chunks, frames, controls):
    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()
    for chunk in chunks:
        diagnostics.record_received_data(chunk)
    diagnostics.increment("not_a_supported_counter")
    assert diagnostics.summary["received_irc_chunk_count"] == len(chunks)
    assert diagnostics.summary["received_irc_frame_count"] == frames
    assert diagnostics.summary["benign_irc_control_frame_count"] == controls
    assert "not_a_supported_counter" not in diagnostics.summary
    assert len(diagnostics._frame_prefix) <= irc_diagnostics._CONTROL_FRAME_PREFIX_LIMIT
    if frames == 3:
        assert diagnostics.summary["keepalive_ping_received_count"] == 1
        assert diagnostics.summary["keepalive_pong_received_count"] == 1


@pytest.mark.parametrize("frame_prefix", ["", " \r\n", ":tmi.twitch.tv"])
def test_control_command_ignores_incomplete_prefixes(frame_prefix: str) -> None:
    assert irc_diagnostics._control_command(frame_prefix) is None


@pytest.mark.parametrize(
    ("message", "error", "operation", "path", "details"),
    [
        ("resource not found", VideoNotFound, None, ["root"], []),
        ("not authorized to view this resource", LoginRequired, None, ["root"], []),
        ("subscription required for this video", VideoUnplayable, None, ["root"], []),
        ("this content was deleted", VideoUnavailable, None, ["root"], []),
        (
            "unexpected failure",
            ParsingError,
            "VideoCommentsByOffsetOrCursor",
            ["video", "comments", 0],
            ["video -> comments -> 0", "VideoCommentsByOffsetOrCursor"],
        ),
        *[
            (
                message,
                graphql_client._PersistedQueryUnavailable,
                "StreamMetadata",
                ["video"],
                [
                    "StreamMetadata",
                    "Operation hashes or required variables may be stale",
                ],
            )
            for message in ("PersistedQueryNotFound", "Persisted query not found")
        ],
    ],
)
def test_handle_gql_errors(message, error, operation, path, details):
    with pytest.raises(error) as exc:
        graphql_client._handle_gql_errors(
            [{"message": message, "path": path}],
            [operation] if operation else None,
        )
    assert all(detail in str(exc.value) for detail in details)


def test_handle_gql_errors_ignores_malformed_error_item() -> None:
    graphql_client._handle_gql_errors(["not-a-dict"])


@pytest.mark.parametrize("batched", [False, True], ids=["dict", "list"])
def test_download_gql_handles_error_response(batched) -> None:
    payload = {"errors": [{"message": "resource not found"}]}

    def session_post(_url, json, headers):
        assert headers["Client-ID"]
        assert json[0]["extensions"]["persistedQuery"]["version"] == 1
        return SimpleNamespace(json=lambda: [payload] if batched else payload)

    with pytest.raises(VideoNotFound):
        graphql_client._download_gql(
            session_post,
            [
                {
                    "operationName": next(iter(graphql_client.OPERATION_HASHES)),
                    "variables": {},
                }
            ],
        )


@pytest.mark.parametrize(
    ("status", "text", "error"),
    [
        (403, "Kasada challenge required", CaptchaChallengeRequired),
        (429, "Too Many Requests", HTTPError),
    ],
)
def test_download_base_gql_errors(status, text, error):
    response = Mock(status_code=status, text=text)
    response.raise_for_status.side_effect = HTTPError(str(status))
    with pytest.raises(error):
        graphql_client._download_base_gql(
            lambda *a, **kw: response, [{"operationName": "x"}]
        )
    if status == 429:
        response.raise_for_status.assert_called_once()


def test_download_gql_rejects_missing_hash_mapping() -> None:
    from chat_downloader.metadata import __version__

    with pytest.raises(ParsingError) as excinfo:
        graphql_client._download_gql(
            Mock(),
            [{"operationName": "NonexistentOperation", "variables": {}}],
        )

    message = str(excinfo.value)
    assert "Missing Twitch persisted GraphQL hash mapping" in message
    # Diagnose the operation, package version, and location of stale hashes.
    assert "NonexistentOperation" in message
    assert __version__ in message
    assert "src/chat_downloader/sites/twitch/constants.py" in message
    assert "persistedQuery" in message


@pytest.fixture
def irc_socket(monkeypatch):
    sock = Mock()
    monkeypatch.setattr(irc_transport, "open_proxied_tls_socket", lambda *a, **kw: sock)
    return sock


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


@pytest.mark.parametrize("split", [False, True])
def test_twitch_chat_irc_registration_and_receive(irc_socket, split):
    text = "hello \U0001f600 world" if split else "hello world"
    encoded = text.encode()
    chunks = [encoded[:8], encoded[8:]] if split else [encoded]
    irc_socket.recv.side_effect = chunks
    irc = irc_transport.TwitchChatIRC()
    assert irc_socket.settimeout.call_args_list == [((None,), {})]
    assert [call.args[0] for call in irc_socket.sendall.call_args_list] == [
        b"CAP REQ :twitch.tv/tags twitch.tv/commands\r\n",
        b"PASS listen\r\n",
        b"NICK justinfan67420\r\n",
    ]
    irc.send_raw("PING")
    assert irc_socket.sendall.call_args.args == (b"PING\r\n",)
    assert "".join(irc.recv(32) for _ in chunks) == text


@pytest.mark.parametrize(
    ("readbuffer", "expected"),
    [
        ("", True),
        ("PING :tmi.twitch.tv\r\nPONG :tmi.twitch.tv\r\n", True),
        (":tmi.twitch.tv PONG tmi.twitch.tv :tmi.twitch.tv\r\n", True),
        (":user!user@user.tmi.twitch.tv JOIN #example\r\n", True),
        (":user!user@user.tmi.twitch.tv PART #example\r\n", True),
        (":tmi.twitch.tv 001 justinfan :Welcome\r\n", True),
        (":tmi.twitch.tv 353 justinfan = #example :justinfan\r\n", True),
        (":tmi.twitch.tv 421 justinfan CAP :Unknown command\r\n", False),
        (":tmi.twitch.tv 433 * justinfan :Nickname in use\r\n", False),
        (
            ":tmi.twitch.tv CAP * ACK :twitch.tv/tags twitch.tv/commands\r\n",
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
    irc = _FakeIRC()
    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()

    for current, previous in [(120.0, 40.0), (150.0, 120.0)]:
        assert (
            irc_transport._maybe_send_keepalive(
                irc,
                current_time=current,
                last_ping_time=previous,
                ping_every=60.0,
                diagnostics=diagnostics,
            )
            == 120.0
        )
        assert irc.sent == ["PING"]
    assert diagnostics.summary["keepalive_ping_sent_count"] == 1


def test_process_irc_buffer_keeps_partial_tail_without_final_newline() -> None:
    full_line = _privmsg("1", "one")
    partial = _privmsg("2", "par")
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


def test_parse_irc_matches_returns_items_and_updated_count():
    payload = "\r\n".join([_privmsg("1", "one"), _privmsg("2", "two"), ""])
    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()
    items, count = irc_transport._parse_irc_matches(
        list(irc_transport.MESSAGE_REGEX.finditer(payload)),
        None,
        249,
        diagnostics=diagnostics,
    )
    assert [item["message"] for item in items] == ["one", "two"]
    assert count == 251
    assert diagnostics.summary["parsed_irc_message_count"] == 2


class _FakeIRC:
    def __init__(self, responses=(), *, send_error=None) -> None:
        self.responses = iter(responses)
        self.sent: list[str] = []
        self.sent_before_recv: list[list[str]] = []
        self.send_error = send_error

    def recv(self, _buffer_size: int) -> str:
        self.sent_before_recv.append(self.sent.copy())
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def send_raw(self, message: str) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(message)


def _stream_messages(irc, diagnostics=None):
    return irc_transport.get_chat_messages_by_stream_id(
        cast("Any", irc),
        "example",
        ChatRequest(url="https://www.twitch.tv/example"),
        diagnostics=diagnostics,
    )


@pytest.mark.parametrize(
    ("chunks", "ping_count"),
    [
        pytest.param(["PING :tmi.twitch.tv\r\n"], 1, id="complete"),
        pytest.param(["PING :tmi.twitch.tv\r", "\n"], 1, id="split"),
        pytest.param(["PING :tmi.twitch.tv\r\n" * 2], 2, id="multiple"),
        pytest.param(
            [_privmsg("1", "PING :tmi.twitch.tv") + "\r\n"],
            0,
            id="chat-payload",
        ),
    ],
)
def test_irc_transport_only_answers_completed_ping_frames(chunks, ping_count) -> None:
    irc = _FakeIRC([*chunks, ""])
    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()
    with pytest.raises(ConnectionError):
        list(_stream_messages(irc, diagnostics))

    assert irc.sent == [irc_transport.PONG_TEXT] * ping_count
    assert diagnostics.summary["received_irc_chunk_count"] == len(chunks)
    assert diagnostics.summary["received_irc_frame_count"] == max(1, ping_count)
    assert diagnostics.summary["keepalive_ping_received_count"] == ping_count
    assert diagnostics.summary["keepalive_pong_sent_count"] == ping_count
    if len(chunks) == 2:
        assert irc.sent_before_recv[1] == []


@pytest.mark.parametrize(
    ("frame", "unknown"),
    [
        (":tmi.twitch.tv PONG tmi.twitch.tv :tmi.twitch.tv\r\n", False),
        ("UNKNOWN LINE\r\n", True),
    ],
)
def test_irc_transport_captures_drift_but_not_control_frames(frame, unknown) -> None:
    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()
    with (
        patch.object(irc_transport, "log") as mock_log,
        patch.object(irc_transport, "capture_debug_sample") as mock_capture,
        pytest.raises(ConnectionError),
    ):
        list(_stream_messages(_FakeIRC([frame, ""]), diagnostics))

    if unknown:
        mock_capture.assert_called_once_with(
            "twitch-unknown-irc-shape",
            {"raw": frame},
            sample_limit=10,
        )
    else:
        mock_log.assert_not_called()
        mock_capture.assert_not_called()
        assert diagnostics.summary["keepalive_pong_received_count"] == 1


@pytest.mark.parametrize(
    ("chunks", "expected", "times", "counters", "sent"),
    [
        (
            [_privmsg("1", "hello") + "\r\n" + _privmsg("2", "part"), "ial\r\n"],
            ["hello", "partial"],
            [0.0, 61.0, 62.0],
            {
                "received_irc_chunk_count": 2,
                "received_irc_frame_count": 2,
                "parsed_irc_message_count": 2,
                "keepalive_ping_sent_count": 1,
            },
            ["PING"],
        ),
        (
            [
                "\r\n".join(_privmsg(str(i), f"message-{i}") for i in range(1, 251))
                + "\r\n"
            ],
            [f"message-{i}" for i in range(1, 251)],
            [0.0, 1.0],
            {},
            [],
        ),
        ([_privmsg("1", "hello") + "\r\nUNKNOWN"], ["hello"], [0.0, 1.0], {}, []),
        (
            [TimeoutError("timed out"), _privmsg("1", "after") + "\r\n"],
            ["after"],
            [0.0, 1.0, 2.0],
            {"receive_timeout_count": 1},
            [],
        ),
    ],
    ids=["split-message-keepalive", "250-messages", "partial-tail", "receive-timeout"],
)
def test_irc_stream_chunks(chunks, expected, times, counters, sent):
    irc = _FakeIRC([*chunks, ""])
    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()
    messages = []
    with (
        patch.object(irc_transport.time, "monotonic", side_effect=times),
        patch.object(irc_transport, "log") as log,
        pytest.raises(ConnectionError),
    ):
        messages.extend(_stream_messages(irc, diagnostics))
    assert [item["message"] for item in messages] == expected
    assert irc.sent == sent
    assert {key: diagnostics.summary[key] for key in counters} == counters
    log.assert_not_called()


def test_irc_transport_maps_socket_receive_error_to_reconnect() -> None:
    with pytest.raises(ConnectionError, match="receive failed"):
        next(_stream_messages(_FakeIRC([OSError("network changed")])))


def test_irc_transport_idle_watchdog_sends_keepalive_then_reconnects() -> None:
    irc = _FakeIRC([TimeoutError(), TimeoutError()])
    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()
    with (
        patch.object(irc_transport.time, "monotonic", side_effect=[0.0, 61.0, 180.0]),
        pytest.raises(ConnectionError, match="became idle"),
    ):
        next(_stream_messages(irc, diagnostics))

    assert irc.sent == ["PING", "PING"]
    assert diagnostics.summary["receive_timeout_count"] == 2
    assert diagnostics.summary["idle_watchdog_expiration_count"] == 1
    assert diagnostics.summary["keepalive_ping_sent_count"] == 2


@pytest.mark.parametrize("frame", ["PING :tmi.twitch.tv\r\n", "UNKNOWN LINE\r\n"])
def test_irc_transport_keepalive_send_errors_trigger_reconnect(frame) -> None:
    irc = _FakeIRC([frame], send_error=OSError("broken pipe"))
    with (
        patch.object(irc_transport.time, "monotonic", side_effect=[0.0, 61.0]),
        pytest.raises(ConnectionError),
    ):
        list(_stream_messages(irc))


def test_twitch_chat_irc_registration_failure_closes_socket(irc_socket):
    irc_socket.sendall.side_effect = OSError("broken pipe")
    with pytest.raises(OSError):
        irc_transport.TwitchChatIRC()
    irc_socket.close.assert_called_once()


def test_twitch_chat_irc_close_is_idempotent(irc_socket):
    irc = irc_transport.TwitchChatIRC()
    irc.set_timeout(1.25)
    irc_socket.settimeout.assert_called_with(1.25)
    irc.close_connection()
    assert irc_socket.sendall.call_args.args == (b"QUIT\r\n",)
    irc_socket.shutdown.assert_called_once_with(irc_transport.socket.SHUT_WR)
    irc_socket.close.assert_called_once()
    irc.close_connection()
    irc_socket.close.assert_called_once()


def test_update_badge_info_skips_malformed_badge_and_keeps_others():
    import base64

    badges = [
        {"id": base64.b64encode(value).decode(), "title": title}
        for value, title in [
            (b"subscriber;6;", "6-Month Sub"),
            (b"notvalid", "Bad Badge"),
        ]
    ]

    def download(_session_post, query, client_id=None):
        items = badges if query[0]["operationName"] == "ChatList_Badges" else []
        return [{"data": {"badges": items, "user": None}}]

    badge_info = {}
    badge_client.update_badge_info(
        session_post=Mock(),
        channel="example",
        download_gql_func=download,
        badge_info=badge_info,
        subscriber_badge_info={},
    )
    assert list(badge_info) == [("subscriber", "6")]
