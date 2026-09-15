# SPDX-License-Identifier: MIT

"""Reviewed live-capture shapes with independent parsing/rendering expectations."""

from __future__ import annotations

import json
from pathlib import Path

from chat_downloader.formatting import ItemFormatter
from chat_downloader.output.capture_parity import audit_capture
from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.sites.twitch.constants import MESSAGE_REGEX
from chat_downloader.sites.twitch.parsing.messages import _parse_irc_item
from chat_downloader.sites.twitch.types import BadgeSet

FIXTURES = Path(__file__).parent / "fixtures" / "twitch" / "live_events"


def test_captured_subscription_and_japanese_text_keep_fields_and_output(tmp_path):
    badge_set = BadgeSet(
        global_badges={("legendus", "1"): {"title": "LEGENDUS"}},
        channel_badges={"123": {("subscriber", "0"): {"title": "Subscriber"}}},
    )
    items = []
    for name in (
        "irc-usernotice-subscription-goal-multimonth.json",
        "irc-privmsg-japanese-badge.json",
    ):
        raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))["raw"]
        match = MESSAGE_REGEX.search(raw)
        assert match is not None
        items.append(_parse_irc_item(match, badge_set=badge_set))

    subscription, text = items
    assert subscription["message_type"] == "subscription"
    assert subscription["cumulative_months"] == 1
    assert subscription["months"] == 0
    assert subscription["multimonth_duration"] == 1
    assert subscription["multimonth_tenure"] == 0
    assert subscription["was_gifted"] is False
    assert subscription["user_wants_to_share_streaks"] is False
    assert subscription["subscription_type"] == "Tier 1"
    assert subscription["subscription_plan_name"] == "Channel Subscription (channel)"
    assert subscription["msg_param_goal_contribution_type"] == "SUB_POINTS"
    # These existing public fields preserve raw IRC strings.
    assert subscription["msg_param_goal_current_contributions"] == "17722"
    assert subscription["msg_param_goal_target_contributions"] == "24000"
    assert subscription["msg_param_goal_user_contributions"] == "1"
    badge = subscription["author"]["badges"][0]
    assert (badge["name"], badge["version"], badge["months"]) == ("subscriber", 0, 1)
    assert badge["title"] == "Subscriber"
    assert "message" not in subscription
    assert text["message_type"] == "text_message"
    assert text["message"] == "日本語のテストです"
    badge = text["author"]["badges"][0]
    assert (badge["name"], badge["version"], badge["title"]) == (
        "legendus",
        1,
        "LEGENDUS",
    )
    assert text["is_first_message"] is False
    assert text["is_returning_chatter"] is False

    # time_text makes the expectation independent of the machine's timezone.
    for item in items:
        item["time_text"] = "0:00"
    expected = [
        "0:00 | (Subscriber) exampleuser subscribed at Tier 1.",
        "0:00 | (LEGENDUS) exampleuser: 日本語のテストです",
    ]
    formatter = ItemFormatter()
    rendered = [formatter.format(item, format_name="twitch") for item in items]
    assert rendered == expected
    jsonl = tmp_path / "chat.jsonl"
    txt = tmp_path / "chat.txt"
    with ContinuousWriter(str(jsonl)) as writer:
        for item in items:
            writer.write(item)
    with ContinuousWriter(str(txt)) as writer:
        for line in rendered:
            writer.write(line)
    assert txt.read_text(encoding="utf-8").splitlines() == expected
    stats = audit_capture(jsonl, txt, formatter=formatter, format_name="twitch")
    assert not stats.failed
    assert stats.jsonl_records == stats.txt_lines == 2
