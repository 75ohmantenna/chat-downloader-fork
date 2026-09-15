# SPDX-License-Identifier: MIT

"""Bounded shape sampling through the real Twitch IRC parser."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

import chat_downloader.redaction as red
from chat_downloader.sites.twitch import irc_diagnostics, irc_transport


def _frame(index, *, emotes=False, reply=False):
    tags = "emotes=25:4-8;" if emotes else "emotes=;"
    if reply:
        tags += "reply-parent-msg-id=parent;reply-parent-user-id=2;"
    return (
        f"@{tags}id={index};display-name=User;user-id=1;tmi-sent-ts=1 "
        ":user!user@user.tmi.twitch.tv PRIVMSG #channel :日本語 Kappa\r\n"
    )


def _parse(capture, frames):
    return irc_transport._parse_irc_matches(
        list(irc_transport.MESSAGE_REGEX.finditer("".join(frames))),
        None,
        0,
        event_frame_capture=capture,
    )[0]


@pytest.mark.parametrize("result", [None, "/sample.json"])
def test_shapes_have_independent_attempt_caps_after_text_event(result, monkeypatch):
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES", "1")
    backend = Mock(return_value=result)
    monkeypatch.setattr(irc_diagnostics, "capture_debug_sample", backend)
    capture = irc_diagnostics._EventDiverseIrcFrameCapture()
    _parse(capture, [_frame(0)])
    # The same sampler survives successive batches/connections. Emote traffic
    # must not exhaust the reply quota, nor the event-key limit stop shapes.
    for index in range(20):
        capture.capture("unknown\r\n", {}, f"UNKNOWN{index}", "")
    _parse(capture, [_frame(i, emotes=True) for i in range(1, 6)])
    _parse(capture, [_frame(i, emotes=True, reply=True) for i in range(6, 11)])
    shape_calls = [c for c in backend.call_args_list if "text-shape" in c.args[0]]
    assert [c.args[0] for c in shape_calls] == [
        *["twitch-irc-text-shape-emotes"] * 3,
        *["twitch-irc-text-shape-in-reply-to"] * 3,
    ]
    assert all(c.args[1]["raw"].endswith("\r\n") for c in shape_calls)
    assert all(
        c.kwargs
        == {
            "sample_limit": 3,
            "sample_group": "twitch-irc-text-shapes",
            "group_limit": 6,
        }
        for c in shape_calls
    )


def test_shapes_overlap_and_require_known_text_and_opt_in(monkeypatch):
    monkeypatch.delenv("CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES", raising=False)
    backend = Mock(return_value="/sample.json")
    monkeypatch.setattr(irc_diagnostics, "capture_debug_sample", backend)
    _parse(irc_diagnostics._EventDiverseIrcFrameCapture(), [_frame(0, emotes=True)])
    backend.assert_not_called()
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES", "1")
    capture = irc_diagnostics._EventDiverseIrcFrameCapture()
    for action, kind in [("USERNOTICE", "text_message"), ("PRIVMSG", "unknown")]:
        capture.capture("frame", {"message_type": kind, "emotes": [1]}, action, "")
    assert not any("text-shape" in c.args[0] for c in backend.call_args_list)
    capture.capture(
        "frame",
        {"message_type": "text_message", "emotes": [1]},
        "PRIVMSG",
        "msg-id=text_message",
    )
    assert not any("text-shape" in c.args[0] for c in backend.call_args_list)
    backend.reset_mock()
    items = _parse(capture, [_frame(1, emotes=True, reply=True)])
    assert items[0]["emotes"][0]["name"] == "Kappa"
    assert items[0]["in_reply_to"]["message_id"] == "parent"
    assert len([c for c in backend.call_args_list if "text-shape" in c.args[0]]) == 2


def test_shape_backend_quotas_survive_new_samplers_in_same_directory(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(tmp_path / "samples"))
    monkeypatch.setattr(red, "_debug_sample_capture_enabled", lambda: True)
    for run in range(2):
        capture = irc_diagnostics._EventDiverseIrcFrameCapture()
        _parse(
            capture, [_frame(run * 10 + i, emotes=True, reply=True) for i in range(5)]
        )
    samples = list((tmp_path / "samples").glob("*.json"))
    assert len([p for p in samples if "text-shape-emotes" in p.name]) == 3
    assert len([p for p in samples if "text-shape-in-reply-to" in p.name]) == 3
    assert len(samples) == 7  # Six shapes plus the ordinary text event.
