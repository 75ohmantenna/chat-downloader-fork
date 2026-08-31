# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chat_downloader.sites.filters import MessageFilter
from chat_downloader.sites.twitch.constants import MESSAGE_GROUPS, MESSAGE_REGEX
from chat_downloader.sites.twitch.parsing.messages import _parse_irc_item
from chat_downloader.sites.twitch.types import BadgeSet

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "twitch" / "live_events"


def _parse_fixture(name: str) -> dict[str, Any]:
    payload = json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))
    match = MESSAGE_REGEX.search(payload["raw"])
    assert match is not None
    return _parse_irc_item(
        match,
        badge_set=BadgeSet(global_badges={}, channel_badges={}),
    )


@pytest.mark.parametrize(
    ("fixture", "message_type", "group"),
    [
        ("irc-usernotice-charity-donation.json", "charity_donation", "charity"),
        (
            "irc-usernotice-gift-sub-base-match.json",
            "gift_subscription_match",
            "subscriptions",
        ),
        (
            "irc-usernotice-one-tap-breakpoint.json",
            "one_tap_breakpoint_achieved",
            "bits",
        ),
        (
            "irc-usernotice-one-tap-gift-redeemed.json",
            "one_tap_gift_redeemed",
            "bits",
        ),
        (
            "irc-usernotice-one-tap-streak-expired.json",
            "one_tap_streak_expired",
            "bits",
        ),
        (
            "irc-usernotice-one-tap-streak-started.json",
            "one_tap_streak_started",
            "bits",
        ),
        (
            "irc-usernotice-moderator-anniversary.json",
            "moderator_anniversary",
            "mods",
        ),
        (
            "irc-usernotice-modiversary.json",
            "moderator_anniversary",
            "mods",
        ),
    ],
)
def test_current_usernotice_types_are_parsed_and_grouped(
    fixture: str,
    message_type: str,
    group: str,
) -> None:
    parsed = _parse_fixture(fixture)

    assert parsed["message_type"] == message_type
    assert MessageFilter(MESSAGE_GROUPS, groups_to_add=[group]).should_add(parsed)
    assert not MessageFilter(
        MESSAGE_GROUPS,
        groups_to_add=["messages"],
    ).should_add(parsed)


def test_paid_pinned_chat_preserves_integer_monetary_components() -> None:
    parsed = _parse_fixture("irc-privmsg-paid-pinned-chat.json")

    assert parsed["message_type"] == "text_message"
    assert parsed["pinned_chat_paid_amount"] == 1250
    assert parsed["pinned_chat_paid_canonical_amount"] == 1250
    assert parsed["pinned_chat_paid_currency"] == "USD"
    assert parsed["pinned_chat_paid_exponent"] == 2
    assert parsed["pinned_chat_paid_level"] == "ONE"
    assert parsed["pinned_chat_paid_is_system_message"] is False
    assert isinstance(parsed["pinned_chat_paid_amount"], int)
    assert isinstance(parsed["pinned_chat_paid_canonical_amount"], int)
    assert isinstance(parsed["pinned_chat_paid_exponent"], int)


def test_charity_and_gift_match_notice_fields_are_typed() -> None:
    charity = _parse_fixture("irc-usernotice-charity-donation.json")
    gift_match = _parse_fixture("irc-usernotice-gift-sub-base-match.json")

    assert charity["charity_name"] == "Example Charity"
    assert charity["donation_amount"] == 2500
    assert charity["donation_currency"] == "USD"
    assert isinstance(charity["donation_amount"], int)
    assert gift_match["advertiser_name"] == "Example Sponsor"
    assert gift_match["gift_subscription_match_quantity"] == 5
    assert isinstance(gift_match["gift_subscription_match_quantity"], int)


def test_one_tap_notice_fields_are_typed_and_bounded_to_three_contributors() -> None:
    breakpoint_notice = _parse_fixture("irc-usernotice-one-tap-breakpoint.json")
    redeemed = _parse_fixture("irc-usernotice-one-tap-gift-redeemed.json")
    expired = _parse_fixture("irc-usernotice-one-tap-streak-expired.json")
    started = _parse_fixture("irc-usernotice-one-tap-streak-started.json")

    assert breakpoint_notice["one_tap_breakpoint_number"] == 3
    assert breakpoint_notice["one_tap_breakpoint_threshold_bits"] == 10000
    assert breakpoint_notice["one_tap_gift_id"] == "gift-123"
    assert redeemed["one_tap_user_display_name"] == "Redeemer"
    assert redeemed["one_tap_bits_spent"] == 500
    assert expired["one_tap_largest_contributor_count"] == 3
    assert expired["one_tap_streak_size_bits"] == 15000
    assert expired["one_tap_streak_size_taps"] == 9
    assert expired["one_tap_contributor_1"] == "Alpha"
    assert expired["one_tap_contributor_1_taps"] == 4
    assert expired["one_tap_contributor_2"] == "Beta"
    assert expired["one_tap_contributor_2_taps"] == 3
    assert expired["one_tap_contributor_3"] == "Gamma"
    assert expired["one_tap_contributor_3_taps"] == 2
    assert started["one_tap_gift_id"] == "gift-999"
    assert started["one_tap_ms_remaining"] == 30000

    typed_fields = [
        breakpoint_notice["one_tap_breakpoint_number"],
        breakpoint_notice["one_tap_breakpoint_threshold_bits"],
        redeemed["one_tap_bits_spent"],
        expired["one_tap_largest_contributor_count"],
        expired["one_tap_streak_size_bits"],
        expired["one_tap_streak_size_taps"],
        expired["one_tap_contributor_1_taps"],
        expired["one_tap_contributor_2_taps"],
        expired["one_tap_contributor_3_taps"],
        started["one_tap_ms_remaining"],
    ]
    assert all(isinstance(value, int) for value in typed_fields)


def test_moderator_anniversary_reuses_typed_months_field() -> None:
    parsed = _parse_fixture("irc-usernotice-moderator-anniversary.json")

    assert parsed["months"] == 24
    assert isinstance(parsed["months"], int)
