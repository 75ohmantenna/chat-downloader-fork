# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from chat_downloader.formatting.format import ItemFormatter
from chat_downloader.sites.twitch.constants import (
    MESSAGE_GROUP_REMAPPINGS,
    MESSAGE_REGEX,
)
from chat_downloader.sites.twitch.parsing.messages import _parse_irc_item

_SUBSCRIPTION_NOTICE_CASES = [
    ("sub", "subscription", False),
    ("resub", "resubscription", False),
    ("subgift", "subscription_gift", False),
    ("anonsubgift", "anonymous_subscription_gift", True),
    ("anonsubmysterygift", "anonymous_mystery_subscription_gift", True),
    ("submysterygift", "mystery_subscription_gift", False),
    ("extendsub", "extend_subscription", False),
    ("standardpayforward", "standard_pay_forward", False),
    ("communitypayforward", "community_pay_forward", False),
    ("primecommunitygiftreceived", "prime_community_gift_received", False),
    ("primepaidupgrade", "prime_paid_upgrade", False),
    ("giftpaidupgrade", "gift_paid_upgrade", False),
    ("rewardgift", "reward_gift", False),
    ("anongiftpaidupgrade", "anonymous_gift_paid_upgrade", True),
]


def _build_usernotice(message_id: str, *, anonymous: bool) -> str:
    tags = [
        "badge-info=",
        "badges=",
        "id=notice-1",
        f"msg-id={message_id}",
        "room-id=123",
        r"system-msg=Subscription\snotice",
        "tmi-sent-ts=1700000000000",
    ]
    if not anonymous:
        tags.extend(
            [
                "display-name=ExampleUser",
                "login=exampleuser",
                "user-id=456",
            ]
        )

    message = "" if anonymous else " :Thanks chat!"
    return f"@{';'.join(tags)} :tmi.twitch.tv USERNOTICE #channel{message}\r\n"


def test_subscription_notice_cases_cover_all_mapped_subscription_families() -> None:
    audited_remappings = {
        **MESSAGE_GROUP_REMAPPINGS["subscriptions"],
        **MESSAGE_GROUP_REMAPPINGS["upgrades"],
    }

    assert {
        (raw_type, message_type)
        for raw_type, message_type, _ in _SUBSCRIPTION_NOTICE_CASES
    } == set(audited_remappings.items())


@pytest.mark.parametrize(
    ("raw_type", "message_type", "anonymous"),
    _SUBSCRIPTION_NOTICE_CASES,
)
def test_parse_and_format_subscription_family_usernotices(
    raw_type: str,
    message_type: str,
    anonymous: bool,
) -> None:
    raw = _build_usernotice(raw_type, anonymous=anonymous)
    match = MESSAGE_REGEX.search(raw)
    assert match is not None

    parsed = _parse_irc_item(match)

    assert parsed["action_type"] == "user_notice"
    assert parsed["message_type"] == message_type
    assert parsed["system_message"] == "Subscription notice"
    if anonymous:
        assert "author" not in parsed
        assert "message" not in parsed
        expected_suffix = "Subscription notice"
    else:
        assert parsed["author"]["name"] == "exampleuser"
        assert parsed["message"] == "Thanks chat!"
        expected_suffix = "Subscription notice — Thanks chat!"

    assert (
        ItemFormatter().format(parsed, format_name="twitch").endswith(expected_suffix)
    )


def test_announcement_keeps_normal_author_and_message_formatting() -> None:
    raw = (
        "@badge-info=;badges=;display-name=Announcer;id=announcement-1;"
        "login=announcer;msg-id=announcement;room-id=123;system-msg=;"
        "tmi-sent-ts=1700000000000;user-id=456 "
        ":tmi.twitch.tv USERNOTICE #channel :An authored announcement\r\n"
    )
    match = MESSAGE_REGEX.search(raw)
    assert match is not None

    parsed = _parse_irc_item(match)

    assert parsed["message_type"] == "announcement"
    assert (
        ItemFormatter()
        .format(parsed, format_name="twitch")
        .endswith("Announcer: An authored announcement")
    )
