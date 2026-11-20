# SPDX-License-Identifier: MIT

"""Drift regression harness for YouTube chat parsing.

Raw continuation fixtures in ``tests/fixtures/youtube/live_events/`` are
replayed through the message pipeline. Each action must emit its expected
message type or report an expected suppression, without logging drift.
Recorded event lists have separate assertions in
``test_youtube_live_event_fixtures_unit.py``.

Workflow for new drift
----------------------
1. Run with ``CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES=1`` and optionally set
   ``CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR``; otherwise capture uses a temp directory.
2. Identify the cause (unknown action, unknown message type, missing key).
3. Fix the parsing code.
4. Review the capture, copy the raw continuation to
   ``tests/fixtures/youtube/live_events/``, and rename it to ``*.json``.
5. Record the expected pipeline results for that fixture and run this harness.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import chat_downloader.debugging as _dbg
import chat_downloader.sites.youtube.parsing.actions_handlers_parser as _ahp
import chat_downloader.sites.youtube.parsing.actions_handlers_validation as _ahv
import chat_downloader.sites.youtube.parsing.actions_router as _ar
import chat_downloader.sites.youtube.parsing.modern_text as _mt
from chat_downloader.sites.filters import MessageFilter
from chat_downloader.sites.youtube.constants_message import _MESSAGE_GROUPS
from chat_downloader.sites.youtube.continuations import (
    parse_continuation_response,
)
from chat_downloader.sites.youtube.message_pipeline import (
    NonEmissionReason,
    process_pipeline_action,
)
from chat_downloader.sites.youtube.parsing.actions_router import iter_action_occurrences
from chat_downloader.utils.json_types import dig

_FX_DIR = Path(__file__).resolve().parent / "fixtures" / "youtube" / "live_events"
_RESTRICTION_FIXTURE = "youtube-replay-restricted-participation.json"

_RECORDED_EVENT_LISTS = {
    "youtube-shapy-crimson-desert-10m-events.json",
    "youtube-shu-pokopia-10m-events.json",
}

_EXPECTED_OUTCOMES: dict[str, tuple[str | NonEmissionReason, ...]] = {
    "mobile-replay-elements.json": (
        "viewer_engagement_message",
        "text_message",
        "paid_message",
        "ticker_paid_message_item",
        "paid_sticker",
        "ticker_paid_sticker_item",
    ),
    "replay-filter-end-boundary.json": ("text_message", "text_message"),
    "youtube-CH0uI-v2Cbc-mobile-element-chat.json": ("text_message",),
    "youtube-IzopCEgh2G8-live-chat-first.json": (
        *("text_message",) * 69,
        "viewer_engagement_message",
        "banner_chat_summary",
    ),
    "youtube-MLmJY7SeISw-jewels-gift-event.json": ("gift_message_view_model",),
    "youtube-S7ugRA3UMjo-mobile-banner.json": ("banner",),
    "youtube-commerce-balance-control.json": (NonEmissionReason.KNOWN_IGNORED_ACTION,),
    "youtube-mobile-new-member.json": ("membership_item",),
    "youtube-mobile-summary-banner.json": ("banner_chat_summary",),
    "youtube-mobile-wrapped-gift.json": ("gift_message_view_model",),
    "youtube-replay-paid-ticker-shared-offset.json": (
        "paid_message",
        "ticker_paid_message_item",
    ),
    "youtube-replay-pinned-banner-timing.json": ("text_message", "banner"),
    _RESTRICTION_FIXTURE: (
        "restricted_participation",
        "restricted_participation",
    ),
}


@pytest.fixture
def drift_recorder(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record unexpected-data diagnostics from the YouTube message parsers."""
    seen: list[str] = []

    def _spy(*args: Any, **_kwargs: Any) -> None:
        seen.append(" ".join(str(a) for a in args))

    monkeypatch.setattr(_dbg, "debug_log", _spy)
    monkeypatch.setattr(_ahp, "debug_log", _spy)
    monkeypatch.setattr(_mt, "debug_log", _spy)
    monkeypatch.setattr(_ar, "debug_log", _spy)
    monkeypatch.setattr(_ahv, "debug_log", _spy)
    return seen


@pytest.mark.parametrize(
    "name",
    sorted(
        {path.name for path in _FX_DIR.glob("*.json")}
        | _EXPECTED_OUTCOMES.keys()
        | _RECORDED_EVENT_LISTS
    ),
)
def test_live_event_fixture(name: str, drift_recorder: list[str]) -> None:
    """Check each discovered fixture against its expected shape and outcomes."""
    payload = json.loads((_FX_DIR / name).read_text(encoding="utf-8"))
    if name in _RECORDED_EVENT_LISTS:
        assert isinstance(payload, list), f"{name} must contain recorded events"
        return

    assert isinstance(payload, dict), f"{name} must contain a raw continuation"
    assert name in _EXPECTED_OUTCOMES, f"{name} needs expected pipeline outcomes"
    raw_actions = dig(
        payload, "continuationContents", "liveChatContinuation", "actions"
    )
    assert isinstance(raw_actions, list), f"{name} must contain an actions list"
    result = parse_continuation_response(payload)
    assert result.actions == raw_actions, f"{name} lost raw continuation actions"
    msg_filter = MessageFilter(
        _MESSAGE_GROUPS, groups_to_add=["all"], types_to_add=None
    )

    actual_outcomes: list[str | NonEmissionReason | None] = []
    for action in iter_action_occurrences(result.actions):
        outcome = process_pipeline_action(action, 0, msg_filter, None)
        actual_outcomes.append(
            outcome.message["message_type"]
            if outcome.message is not None
            else outcome.non_emission_reason
        )

    assert not drift_recorder, f"{name} triggered drift log(s):\n" + "\n".join(
        f"  {line}" for line in drift_recorder
    )
    assert actual_outcomes == list(_EXPECTED_OUTCOMES[name])


@pytest.mark.parametrize(
    ("key_path", "value"),
    [
        (("toReplace",), "ANOTHER_PANEL"),
        (("replacement",), {"liveChatUnexpectedRenderer": {}}),
        (("replacement", "liveChatUnexpectedRenderer"), {}),
        (("replacement", "liveChatRestrictedParticipationRenderer"), {}),
        (
            ("replacement", "liveChatRestrictedParticipationRenderer", "message"),
            {"runs": [{}]},
        ),
    ],
)
def test_invalid_panel_replacement_is_diagnosed_without_emission(
    key_path, value, drift_recorder
):
    payload = json.loads((_FX_DIR / _RESTRICTION_FIXTURE).read_text(encoding="utf-8"))
    action = parse_continuation_response(payload).actions[0]
    command = action["replayChatItemAction"]["actions"][0][
        "replaceLiveChatRendererAction"
    ]
    for key in key_path[:-1]:
        command = command[key]
    command[key_path[-1]] = value
    msg_filter = MessageFilter(_MESSAGE_GROUPS, groups_to_add=["all"])

    outcome = process_pipeline_action(action, 0, msg_filter, None)

    assert outcome.non_emission_reason is NonEmissionReason.INVALID_MESSAGE
    assert outcome.message is None
    assert drift_recorder == [
        "No message type Action type: replaceLiveChatRendererAction"
    ]


def test_panel_notice_tolerates_malformed_optional_icon(drift_recorder):
    payload = json.loads((_FX_DIR / _RESTRICTION_FIXTURE).read_text(encoding="utf-8"))
    action = parse_continuation_response(payload).actions[0]
    command = action["replayChatItemAction"]["actions"][0][
        "replaceLiveChatRendererAction"
    ]
    command["replacement"]["liveChatRestrictedParticipationRenderer"]["icon"] = []
    msg_filter = MessageFilter(_MESSAGE_GROUPS, groups_to_add=["moderation"])

    outcome = process_pipeline_action(action, 0, msg_filter, None)

    assert outcome.message is not None
    assert outcome.message["message"] == "Subscribers-only mode was enabled"
    assert "icon" not in outcome.message
    assert not drift_recorder
