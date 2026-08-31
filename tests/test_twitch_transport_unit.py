# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import Mock, patch

import pytest

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

if TYPE_CHECKING:
    from pathlib import Path


def _privmsg(message_id: str, text: str) -> str:
    return (
        "@badge-info=;badges=;color=;display-name=User;emotes=;id="
        f"{message_id};mod=0;room-id=1;subscriber=0;tmi-sent-ts=1;turbo=0;user-id=1;"
        f"user-type= :user!user@user.tmi.twitch.tv PRIVMSG #example :{text}"
    )


def _usernotice(message_id: str, message_type: str, text: str) -> str:
    return (
        "@badge-info=;badges=;color=;display-name=User;emotes=;flags=;id="
        f"{message_id};mod=0;msg-id={message_type};room-id=1;subscriber=1;"
        "system-msg=Event;tmi-sent-ts=1;turbo=0;user-id=1;user-type= "
        f":tmi.twitch.tv USERNOTICE #example :{text}"
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


def test_event_frame_capture_requires_explicit_scope_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES",
        raising=False,
    )
    captured = []
    monkeypatch.setattr(
        irc_diagnostics,
        "capture_debug_sample",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )

    irc_diagnostics._EventDiverseIrcFrameCapture().capture(
        "valid frame\r\n",
        {"message_type": "resubscription"},
        "USERNOTICE",
        "msg-id=resub",
    )

    assert captured == []


def test_event_frame_capture_prefers_message_type_and_falls_back_to_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = []

    def record_capture(*args, **kwargs):
        captured.append((args, kwargs))
        return f"/samples/{len(captured)}.json"

    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES", "yes")
    monkeypatch.setattr(
        irc_diagnostics,
        "capture_debug_sample",
        record_capture,
    )
    frame_capture = irc_diagnostics._EventDiverseIrcFrameCapture()

    frame_capture.capture(
        "resub one\r\n",
        {"message_type": "resubscription"},
        "USERNOTICE",
        "msg-id=resub",
    )
    frame_capture.capture(
        "resub two\r\n",
        {"message_type": "resubscription"},
        "USERNOTICE",
        "msg-id=resub",
    )
    frame_capture.capture(
        "milestone\r\n",
        {"message_type": "viewermilestone"},
        "USERNOTICE",
        "msg-id=viewermilestone",
    )
    frame_capture.capture("notice\r\n", {}, "NOTICE", "")

    assert captured == [
        (
            (
                "twitch-irc-event-message-resubscription-7dce7b9831c9",
                {"raw": "resub one\r\n"},
            ),
            {
                "sample_limit": 1,
                "sample_group": "twitch-irc-event-frames",
                "group_limit": 12,
            },
        ),
        (
            (
                "twitch-irc-event-message-viewermilestone-71b63634a922",
                {"raw": "milestone\r\n"},
            ),
            {
                "sample_limit": 1,
                "sample_group": "twitch-irc-event-frames",
                "group_limit": 12,
            },
        ),
        (
            (
                "twitch-irc-event-action-notice-dfb14fbb9e7d",
                {"raw": "notice\r\n"},
            ),
            {
                "sample_limit": 1,
                "sample_group": "twitch-irc-event-frames",
                "group_limit": 12,
            },
        ),
    ]


def test_event_frame_capture_bounds_provider_controlled_keys_and_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = []

    def record_capture(*args, **kwargs):
        captured.append((args, kwargs))
        return f"/samples/{len(captured)}.json"

    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES", "1")
    monkeypatch.setattr(
        irc_diagnostics,
        "capture_debug_sample",
        record_capture,
    )
    frame_capture = irc_diagnostics._EventDiverseIrcFrameCapture()

    for index in range(20):
        attacker_value = f"../Provider\\Type-{index}-" + "x" * 500
        frame_capture.capture(
            f"frame {index}\r\n",
            {},
            attacker_value,
            "",
        )

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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sample_dir = tmp_path / "samples"
    canary = "SuperSecretCredential123"
    raw_action = f"Authorization=Bearer{canary}"
    raw_frame = f"@room-id=1 :provider.test {raw_action} #example :public message\r\n"
    captured_labels: list[str] = []
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(sample_dir))
    monkeypatch.setattr(red, "_debug_sample_capture_enabled", lambda: True)
    caplog.set_level(logging.DEBUG, logger=red._get_logger().name)

    def capture_real_sample(label, payload, **kwargs):
        captured_labels.append(label)
        return red.capture_debug_sample(label, payload, **kwargs)

    monkeypatch.setattr(
        irc_diagnostics,
        "capture_debug_sample",
        capture_real_sample,
    )
    frame_capture = irc_diagnostics._EventDiverseIrcFrameCapture()
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
        key = irc_diagnostics._event_capture_key(
            {"message_type": normalized_type},
            "USERNOTICE",
            f"room-id=1;msg-id={raw_msg_id};user-id=2",
        )
        assert key.startswith("message-")
        assert keys_by_normalized_type.setdefault(normalized_type, key) == key

    assert len(set(keys_by_normalized_type.values())) == len(keys_by_normalized_type)
    for raw_action, normalized_type in irc_diagnostics.ACTION_TYPE_REMAPPING.items():
        key = irc_diagnostics._event_capture_key(
            {"message_type": normalized_type},
            raw_action,
            "room-id=1;user-id=2",
        )
        assert key.startswith("message-")

    assert irc_diagnostics._event_capture_key(
        {"message_type": "resubscription"},
        "USERNOTICE",
        "msg-id=resub",
    ).startswith("message-resubscription-")
    assert irc_diagnostics._event_capture_key(
        {"message_type": "resubscription"},
        "USERNOTICE",
        "msg-id=resubscription",
    ).startswith("action-usernotice-")
    assert irc_diagnostics._event_capture_key(
        {"message_type": "resubscription"},
        "USERNOTICE",
        "msg-id=RESUB",
    ).startswith("action-usernotice-")
    assert irc_diagnostics._event_capture_key(
        {"message_type": "text_message"},
        "USERNOTICE",
        "msg-id=text_message",
    ).startswith("action-usernotice-")
    assert irc_diagnostics._event_capture_key(
        {"message_type": "text_message"},
        "text_message",
        "room-id=1",
    ).startswith("action-unknown-")
    assert irc_diagnostics._event_capture_key(
        {"message_type": "resubscription"},
        "USERNOTICE",
        "msg-id=resub;msg-id=resubscription",
    ).startswith("action-usernotice-")


def test_event_capture_retries_transient_failure_before_marking_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = iter([None, "/samples/success.json"])
    capture_calls = []
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES", "1")

    def capture_with_transient_failure(*args, **kwargs):
        capture_calls.append((args, kwargs))
        return next(results)

    monkeypatch.setattr(
        irc_diagnostics,
        "capture_debug_sample",
        capture_with_transient_failure,
    )
    frame_capture = irc_diagnostics._EventDiverseIrcFrameCapture()

    for index in range(3):
        frame_capture.capture(
            f"resub {index}\r\n",
            {"message_type": "resubscription"},
            "USERNOTICE",
            "msg-id=resub",
        )

    event_key = irc_diagnostics._event_capture_key(
        {"message_type": "resubscription"},
        "USERNOTICE",
        "msg-id=resub",
    )
    assert len(capture_calls) == 2
    assert frame_capture._event_key_attempts == {event_key: 2}
    assert frame_capture._captured_event_keys == {event_key}


def test_event_capture_permanent_failures_have_bounded_attempts_and_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture_calls = []
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES", "1")
    monkeypatch.setattr(
        irc_diagnostics,
        "capture_debug_sample",
        lambda *args, **kwargs: capture_calls.append((args, kwargs)),
    )
    frame_capture = irc_diagnostics._EventDiverseIrcFrameCapture()

    for index in range(20):
        for attempt in range(4):
            frame_capture.capture(
                f"frame {index}-{attempt}\r\n",
                {},
                f"ACTION-{index}",
                "",
            )

    assert len(capture_calls) == 24
    assert len(frame_capture._event_key_attempts) == 12
    assert set(frame_capture._event_key_attempts.values()) == {2}
    assert frame_capture._captured_event_keys == set()


def test_event_capture_backend_group_is_shared_by_directory_across_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sample_dir = tmp_path / "samples"
    known_types = sorted(irc_diagnostics._KNOWN_NORMALIZED_MESSAGE_TYPES)
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(sample_dir))
    monkeypatch.setattr(red, "_debug_sample_capture_enabled", lambda: True)

    first_run_capture = irc_diagnostics._EventDiverseIrcFrameCapture()
    for index, message_type in enumerate(known_types[:12]):
        first_run_capture.capture(
            f"first run {index}\r\n",
            {"message_type": message_type},
            "USERNOTICE",
            "",
        )

    later_run_capture = irc_diagnostics._EventDiverseIrcFrameCapture()
    for index in range(3):
        later_run_capture.capture(
            f"later run {index}\r\n",
            {"message_type": known_types[12]},
            "USERNOTICE",
            "",
        )

    assert len(list(sample_dir.glob("*.json"))) == 12
    assert len(first_run_capture._captured_event_keys) == 12
    assert later_run_capture._captured_event_keys == set()
    assert set(later_run_capture._event_key_attempts.values()) == {2}


def test_event_capture_backend_label_persists_across_runs_with_group_slots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sample_dir = tmp_path / "samples"
    backend_paths: list[str | None] = []
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(sample_dir))
    monkeypatch.setattr(red, "_debug_sample_capture_enabled", lambda: True)

    def record_backend_path(*args, **kwargs):
        path = red.capture_debug_sample(*args, **kwargs)
        backend_paths.append(path)
        return path

    monkeypatch.setattr(
        irc_diagnostics,
        "capture_debug_sample",
        record_backend_path,
    )

    first_run_capture = irc_diagnostics._EventDiverseIrcFrameCapture()
    first_run_capture.capture(
        "same payload\r\n",
        {"message_type": "resubscription"},
        "USERNOTICE",
        "msg-id=resub",
    )

    exact_repeat_capture = irc_diagnostics._EventDiverseIrcFrameCapture()
    exact_repeat_capture.capture(
        "same payload\r\n",
        {"message_type": "resubscription"},
        "USERNOTICE",
        "msg-id=resub",
    )

    changed_payload_capture = irc_diagnostics._EventDiverseIrcFrameCapture()
    for index in range(3):
        changed_payload_capture.capture(
            f"different payload {index}\r\n",
            {"message_type": "resubscription"},
            "USERNOTICE",
            "msg-id=resub",
        )

    available_group_slot_capture = irc_diagnostics._EventDiverseIrcFrameCapture()
    available_group_slot_capture.capture(
        "different event\r\n",
        {"message_type": "viewermilestone"},
        "USERNOTICE",
        "msg-id=viewermilestone",
    )

    assert backend_paths[0] is not None
    assert backend_paths[1] == backend_paths[0]
    assert backend_paths[2:4] == [None, None]
    assert backend_paths[4] is not None
    assert len(list(sample_dir.glob("*.json"))) == 2
    assert changed_payload_capture._captured_event_keys == set()
    assert set(changed_payload_capture._event_key_attempts.values()) == {2}


def test_real_parser_raw_msg_id_provenance_prevents_normalized_masquerades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = []

    def record_capture(*args, **kwargs):
        captured.append((args, kwargs))
        return f"/samples/{len(captured)}.json"

    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES", "1")
    monkeypatch.setattr(irc_diagnostics, "capture_debug_sample", record_capture)

    resub_frames = [
        _usernotice("genuine-resub", "resub", "Genuine resub"),
        _usernotice("masquerade-resub", "resubscription", "Unknown raw type"),
    ]
    resub_matches = list(
        irc_transport.MESSAGE_REGEX.finditer("\r\n".join(resub_frames) + "\r\n")
    )
    resub_items, _message_count = irc_transport._parse_irc_matches(
        resub_matches,
        None,
        0,
        event_frame_capture=irc_diagnostics._EventDiverseIrcFrameCapture(),
    )

    text_frames = [
        _privmsg("genuine-text", "Genuine text"),
        _usernotice("masquerade-text", "text_message", "Unknown raw type"),
    ]
    text_matches = list(
        irc_transport.MESSAGE_REGEX.finditer("\r\n".join(text_frames) + "\r\n")
    )
    text_items, _message_count = irc_transport._parse_irc_matches(
        text_matches,
        None,
        0,
        event_frame_capture=irc_diagnostics._EventDiverseIrcFrameCapture(),
    )

    assert [item["message_type"] for item in resub_items] == [
        "resubscription",
        "resubscription",
    ]
    assert [item["message_type"] for item in text_items] == [
        "text_message",
        "text_message",
    ]
    assert [args[0] for args, _kwargs in captured] == [
        "twitch-irc-event-message-resubscription-7dce7b9831c9",
        "twitch-irc-event-action-usernotice-541488f4d6e7",
        "twitch-irc-event-message-text-message-18e44952e1aa",
        "twitch-irc-event-action-usernotice-541488f4d6e7",
    ]


def test_real_parser_unknown_types_share_raw_action_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = []

    def record_capture(*args, **kwargs):
        captured.append((args, kwargs))
        return f"/samples/{len(captured)}.json"

    raw_frames = [
        _usernotice("unknown-1", "unknown-one", "First unknown"),
        _usernotice("unknown-2", "unknown-two", "Second unknown"),
        (
            "@badge-info=;badges=;display-name=User;room-id=1;tmi-sent-ts=1;"
            "user-id=1 :tmi.twitch.tv MYSTERY #example :Unknown action"
        ),
    ]
    matches = list(
        irc_transport.MESSAGE_REGEX.finditer("\r\n".join(raw_frames) + "\r\n")
    )
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES", "1")
    monkeypatch.setattr(irc_diagnostics, "capture_debug_sample", record_capture)

    items, _message_count = irc_transport._parse_irc_matches(
        matches,
        None,
        0,
        event_frame_capture=irc_diagnostics._EventDiverseIrcFrameCapture(),
    )

    assert [item["message_type"] for item in items] == [
        "unknown-one",
        "unknown-two",
        "MYSTERY",
    ]
    assert [args[0] for args, _kwargs in captured] == [
        "twitch-irc-event-action-usernotice-541488f4d6e7",
        "twitch-irc-event-action-unknown-9a26a76fee31",
    ]


def test_successful_capture_modes_have_additive_fifteen_frame_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = []

    def record_capture(*args, **kwargs):
        captured.append((args, kwargs))
        return f"/samples/{len(captured)}.json"

    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_FRAMES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES", "1")
    monkeypatch.setattr(
        irc_diagnostics,
        "capture_debug_sample",
        record_capture,
    )
    first_frame_capture = irc_diagnostics._SuccessfulIrcFrameCapture()
    event_frame_capture = irc_diagnostics._EventDiverseIrcFrameCapture()

    for index in range(20):
        raw_frame = f"frame {index}\r\n"
        first_frame_capture.capture(raw_frame)
        event_frame_capture.capture(
            raw_frame,
            {},
            f"ACTION-{index}",
            "",
        )

    assert len(captured) == 15
    assert [args[1]["raw"] for args, _kwargs in captured].count("frame 0\r\n") == 2
    assert sum(args[0] == "twitch-irc-frame" for args, _kwargs in captured) == 3
    assert (
        sum(args[0].startswith("twitch-irc-event-") for args, _kwargs in captured) == 12
    )


def test_live_diagnostics_count_split_control_frames_with_bounded_state() -> None:
    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()

    diagnostics.record_received_data("PING :tmi.twitch.tv\r")
    diagnostics.record_received_data(
        "\n:tmi.twitch.tv PONG tmi.twitch.tv :tmi.twitch.tv\r\n"
    )
    diagnostics.record_received_data("x" * 100 + "PING :tmi.twitch.tv\r\n")
    diagnostics.increment("not_a_supported_counter")

    assert diagnostics.summary["received_irc_chunk_count"] == 3
    assert diagnostics.summary["received_irc_frame_count"] == 3
    assert diagnostics.summary["benign_irc_control_frame_count"] == 2
    assert diagnostics.summary["keepalive_ping_received_count"] == 1
    assert diagnostics.summary["keepalive_pong_received_count"] == 1
    assert "not_a_supported_counter" not in diagnostics.summary
    assert len(diagnostics._frame_prefix) <= (
        irc_diagnostics._CONTROL_FRAME_PREFIX_LIMIT
    )


def test_live_diagnostics_separate_benign_control_and_message_frames() -> None:
    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()

    diagnostics.record_received_data(
        ":tmi.twitch.tv 001 justinfan :Welcome\r\n"
        ":tmi.twitch.tv CAP * ACK :twitch.tv/tags twitch.tv/commands\r\n"
        ":user!user@user.tmi.twitch.tv JOIN #example\r\n"
        "@badge-info=;badges= :user!user@user.tmi.twitch.tv "
        "PRIVMSG #example :JOIN #another-channel\r\n"
        "@badge-info= :user!user@user.tmi.twitch.tv JOIN #example\r\n"
        ":tmi.twitch.tv 421 justinfan CAP :Unknown command\r\n"
        "UNKNOWN LINE\r\n",
    )

    assert diagnostics.summary["received_irc_frame_count"] == 7
    assert diagnostics.summary["benign_irc_control_frame_count"] == 3


@pytest.mark.parametrize("frame_prefix", ["", " \r\n", ":tmi.twitch.tv"])
def test_control_command_ignores_incomplete_prefixes(frame_prefix: str) -> None:
    assert irc_diagnostics._control_command(frame_prefix) is None


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
        ((b"CAP REQ :twitch.tv/tags twitch.tv/commands\r\n",), {}),
        ((b"PASS listen\r\n",), {}),
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
    class FakeIRC:
        def __init__(self) -> None:
            self.sent = []

        def send_raw(self, message: str) -> None:
            self.sent.append(message)

    irc = FakeIRC()
    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()

    assert (
        irc_transport._maybe_send_keepalive(
            irc,
            current_time=120.0,
            last_ping_time=40.0,
            ping_every=60.0,
            diagnostics=diagnostics,
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
            diagnostics=diagnostics,
        )
        == 120.0
    )
    assert irc.sent == ["PING"]
    assert diagnostics.summary["keepalive_ping_sent_count"] == 1


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

    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()
    items, message_count = irc_transport._parse_irc_matches(
        matches,
        None,
        249,
        diagnostics=diagnostics,
    )

    assert items == [{"message": "one"}, {"message": "two"}]
    assert message_count == 251
    assert diagnostics.summary["parsed_irc_message_count"] == 2


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
    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()

    with pytest.raises(ConnectionError):
        list(
            irc_transport.get_chat_messages_by_stream_id(
                cast("Any", irc),
                "example",
                ChatRequest(url="https://www.twitch.tv/example"),
                diagnostics=diagnostics,
            ),
        )

    assert irc.sent == [irc_transport.PONG_TEXT]
    assert diagnostics.summary["received_irc_chunk_count"] == 1
    assert diagnostics.summary["received_irc_frame_count"] == 1
    assert diagnostics.summary["keepalive_ping_received_count"] == 1
    assert diagnostics.summary["keepalive_pong_sent_count"] == 1


def test_irc_transport_waits_for_complete_ping_before_sending_pong() -> None:
    class FakeIRC:
        def __init__(self) -> None:
            self.responses = iter(["PING :tmi.twitch.tv\r", "\n", ""])
            self.sent: list[str] = []

        def recv(self, _buffer_size: int) -> str:
            response = next(self.responses)
            if response == "\n":
                assert self.sent == []
            return response

        def send_raw(self, message: str) -> None:
            self.sent.append(message)

    irc = FakeIRC()
    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()

    with pytest.raises(ConnectionError):
        list(
            irc_transport.get_chat_messages_by_stream_id(
                cast("Any", irc),
                "example",
                ChatRequest(url="https://www.twitch.tv/example"),
                diagnostics=diagnostics,
            )
        )

    assert irc.sent == [irc_transport.PONG_TEXT]
    assert diagnostics.summary["received_irc_frame_count"] == 1
    assert diagnostics.summary["keepalive_ping_received_count"] == 1
    assert diagnostics.summary["keepalive_pong_sent_count"] == 1


def test_irc_transport_sends_one_pong_per_completed_ping() -> None:
    class FakeIRC:
        def __init__(self) -> None:
            self.responses = iter(
                ["PING :tmi.twitch.tv\r\nPING :tmi.twitch.tv\r\n", ""]
            )
            self.sent: list[str] = []

        def recv(self, _buffer_size: int) -> str:
            return next(self.responses)

        def send_raw(self, message: str) -> None:
            self.sent.append(message)

    irc = FakeIRC()
    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()

    with pytest.raises(ConnectionError):
        list(
            irc_transport.get_chat_messages_by_stream_id(
                cast("Any", irc),
                "example",
                ChatRequest(url="https://www.twitch.tv/example"),
                diagnostics=diagnostics,
            )
        )

    assert irc.sent == [irc_transport.PONG_TEXT, irc_transport.PONG_TEXT]
    assert diagnostics.summary["keepalive_ping_received_count"] == 2
    assert diagnostics.summary["keepalive_pong_sent_count"] == 2


def test_irc_transport_ignores_ping_text_in_chat_payload() -> None:
    class FakeIRC:
        def __init__(self) -> None:
            self.responses = iter([_privmsg("1", "PING :tmi.twitch.tv") + "\r\n", ""])
            self.sent: list[str] = []

        def recv(self, _buffer_size: int) -> str:
            return next(self.responses)

        def send_raw(self, message: str) -> None:
            self.sent.append(message)

    irc = FakeIRC()
    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()

    with pytest.raises(ConnectionError):
        list(
            irc_transport.get_chat_messages_by_stream_id(
                cast("Any", irc),
                "example",
                ChatRequest(url="https://www.twitch.tv/example"),
                diagnostics=diagnostics,
            )
        )

    assert irc.sent == []
    assert diagnostics.summary["keepalive_ping_received_count"] == 0
    assert diagnostics.summary["keepalive_pong_sent_count"] == 0


def test_irc_transport_ignores_prefixed_pong_without_drift_capture() -> None:
    class FakeIRC:
        def __init__(self) -> None:
            self.responses = iter(
                [":tmi.twitch.tv PONG tmi.twitch.tv :tmi.twitch.tv\r\n", ""]
            )

        def recv(self, _buffer_size: int) -> str:
            return next(self.responses)

        def send_raw(self, _message: str) -> None:
            return None

    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()
    with (
        patch.object(irc_transport, "log") as mock_log,
        patch.object(irc_transport, "capture_debug_sample") as mock_capture,
        pytest.raises(ConnectionError),
    ):
        list(
            irc_transport.get_chat_messages_by_stream_id(
                cast("Any", FakeIRC()),
                "example",
                ChatRequest(url="https://www.twitch.tv/example"),
                diagnostics=diagnostics,
            )
        )

    mock_log.assert_not_called()
    mock_capture.assert_not_called()
    assert diagnostics.summary["keepalive_pong_received_count"] == 1


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
    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()
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
                diagnostics=diagnostics,
            ),
        ) == [{"message": "hello"}, {"message": "partial"}]

    assert mock_parse.call_count == 2
    mock_log.assert_not_called()
    assert irc.sent == ["PING"]
    assert diagnostics.summary["received_irc_chunk_count"] == 2
    assert diagnostics.summary["received_irc_frame_count"] == 2
    assert diagnostics.summary["parsed_irc_message_count"] == 2
    assert diagnostics.summary["keepalive_ping_sent_count"] == 1


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
    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()
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
                diagnostics=diagnostics,
            )
        )

    assert irc.sent == ["PING", "PING"]
    assert diagnostics.summary["receive_timeout_count"] == 2
    assert diagnostics.summary["idle_watchdog_expiration_count"] == 1
    assert diagnostics.summary["keepalive_ping_sent_count"] == 2
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

    badge_client.update_badge_info(
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
