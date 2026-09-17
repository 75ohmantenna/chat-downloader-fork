# SPDX-License-Identifier: MIT

from __future__ import annotations

from copy import deepcopy

import pytest

from chat_downloader.sites.twitch.constants import (
    MESSAGE_GROUPS,
    MESSAGE_TYPE_REMAPPING,
)
from chat_downloader.sites.twitch.parsing import (
    message_emotes as tw_emotes,
)
from chat_downloader.sites.twitch.parsing import (
    message_irc_resolve as tw_irc_resolve,
)
from chat_downloader.sites.twitch.parsing import (
    messages as tw_messages,
)
from chat_downloader.sites.twitch.parsing.tag_decoding import (
    _decode_pseudo_bnf,
    _parse_bool,
    _parse_bool_text,
)
from chat_downloader.sites.twitch.remappings import build_comment_remapping
from chat_downloader.sites.twitch.types import BadgeSet
from chat_downloader.sites.twitch.validation_keys import build_known_irc_keys
from tests.twitch_third_helpers import (
    badge_record,
    gql_comment,
    gql_message,
    irc_frame,
)
from tests.twitch_third_helpers import (
    parse_irc as _parse_irc,
)


@pytest.mark.parametrize(
    ("raw_type", "message_type", "group"),
    [
        ("announcement", "announcement", "messages"),
        ("animated-message", "animated-message", "messages"),
        ("gigantified-emote-message", "gigantified-emote-message", "messages"),
        ("socialsharingbadge", "social_sharing_badge", "messages"),
        ("sharedchatnotice", "shared_chat_notice", "other"),
    ],
)
def test_message_type_mapping_and_group(raw_type, message_type, group) -> None:
    assert MESSAGE_TYPE_REMAPPING[raw_type] == message_type
    assert message_type in MESSAGE_GROUPS[group]


def test_social_sharing_badge_level_is_known() -> None:
    assert "current_badge_level" in build_known_irc_keys()


@pytest.mark.parametrize(
    ("tags", "text", "expected"),
    [
        (
            (
                "display-name=StreamElements;badges=moderator/1,partner/1;"
                "msg-id=announcement;msg-param-color=PRIMARY;system-msg="
            ),
            "announcement text",
            {"message_type": "announcement", "announcement_colour": "PRIMARY"},
        ),
        (
            (
                "badge-info=subscriber/3;badges=subscriber/3;login=socialbadgeuser;"
                "display-name=SocialBadgeUser;subscriber=1;"
                "msg-id=socialsharingbadge;msg-param-current-badge-level=1;"
                r"system-msg=Unlocked\sa\ssocial\ssharing\sbadge"
            ),
            "Wooohooo, I got a social media badge!",
            {"message_type": "social_sharing_badge", "current_badge_level": 1},
        ),
    ],
)
def test_parse_usernotice(tags, text, expected):
    parsed = _parse_irc(irc_frame(tags, "USERNOTICE", text, user=""))
    assert parsed["action_type"] == "user_notice"
    assert parsed["message"] == text
    assert {key: parsed[key] for key in expected} == expected
    assert parsed["author"]["name"] == (
        "streamelements"
        if expected["message_type"] == "announcement"
        else "socialbadgeuser"
    )


@pytest.mark.parametrize("badge", ["moderator", "subscriber"])
def test_parse_irc_item_parses_shared_chat_privmsg_tags(badge) -> None:
    parsed = _parse_irc(
        irc_frame(
            "badges=vip/1;room-id=123;source-id=shared-message-1;source-room-id=456;"
            f"source-badges={badge}/1;source-badge-info=subscriber/12;source-only=1"
        )
    )

    assert parsed["shared_chat_source_message_id"] == "shared-message-1"
    assert parsed["shared_chat_source_channel_id"] == "456"
    assert parsed["shared_chat_source_badges"][0]["name"] == badge
    assert parsed["shared_chat_source_badges"][0]["version"] == 1
    assert parsed["shared_chat_source_only"] is True
    assert parsed["is_shared_chat_message"] is True
    assert parsed["shared_chat_effective_source_channel_id"] == "456"
    assert parsed["shared_chat_is_cross_channel"] is True

    if badge == "subscriber":
        assert parsed["shared_chat_source_badges"][0]["months"] == 12


@pytest.mark.parametrize("notice", [False, True])
def test_parse_shared_chat_usernotice(notice):
    tags = "source-msg-id=announcement;msg-id=announcement"
    if notice:
        tags += (
            ";source-id=shared-message-1;source-room-id=123456;source-only=1;"
            r"msg-id=sharedchatnotice;system-msg=Shared\sChat\snotice"
        )
    parsed = _parse_irc(irc_frame(tags, "USERNOTICE", "promo", user=""))
    assert parsed["shared_chat_source_msg_id"] == "announcement"
    assert parsed["message_type"] == (
        "shared_chat_notice" if notice else "announcement"
    )
    if notice:
        assert parsed["action_type"] == "user_notice"
        assert parsed["system_message"] == "Shared Chat notice"
        assert parsed["shared_chat_source_message_id"] == "shared-message-1"
        assert parsed["shared_chat_source_channel_id"] == "123456"
        assert parsed["shared_chat_source_only"] is True
        assert parsed["is_shared_chat_message"] is True
        assert parsed["shared_chat_effective_source_channel_id"] == "123456"
        assert parsed["shared_chat_is_cross_channel"] is True


def test_parse_irc_item_preserves_sharedchatnotice_goal_params() -> None:
    parsed = _parse_irc(
        irc_frame(
            "source-room-id=123456;source-msg-id=announcement;msg-id=sharedchatnotice;"
            "msg-param-goal-target-contributions=100;"
            "msg-param-goal-current-contributions=25;msg-param-goal-user-contributions=5;"
            r"msg-param-goal-description=Daily\sgoal;msg-param-goal-contribution-type=BITS",
            "USERNOTICE",
            "promo",
            user="",
        )
    )

    assert parsed["message_type"] == "shared_chat_notice"
    assert parsed["msg_param_goal_target_contributions"] == "100"
    assert parsed["msg_param_goal_current_contributions"] == "25"
    assert parsed["msg_param_goal_user_contributions"] == "5"
    assert parsed["msg_param_goal_description"] == r"Daily\sgoal"
    assert parsed["msg_param_goal_contribution_type"] == "BITS"


def test_parse_irc_item_sets_shared_chat_fields_for_same_channel_source() -> None:
    parsed = _parse_irc(
        irc_frame(
            "badges=vip/1;room-id=123;source-id=shared-message-1;source-room-id=123"
        )
    )

    assert parsed["is_shared_chat_message"] is True
    assert parsed["shared_chat_effective_source_channel_id"] == "123"
    assert parsed["shared_chat_is_cross_channel"] is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(r"hello\sworld\:\:", "hello world;;"), (r"a\\b", r"a\b")],
)
def test_decode_pseudo_bnf(raw, expected) -> None:
    assert _decode_pseudo_bnf(raw) == expected


def test_parse_bool_and_bool_text() -> None:
    assert _parse_bool("1") is True
    assert _parse_bool("0") is False
    assert _parse_bool_text("true") is True
    assert _parse_bool_text("false") is False


def test_generate_emote_image_list_shapes() -> None:
    images = tw_emotes._generate_emote_image_list("25")
    # 2 themes * 3 sizes
    assert len(images) == 6
    ids = {img["id"] for img in images}
    assert "28x28-light" in ids
    assert "112x112-dark" in ids


def test_parse_emotes_from_tag_text() -> None:
    parsed = tw_emotes._parse_emotes("25:0-4,6-10/1902:12-15")
    assert len(parsed) == 2
    assert parsed[0]["id"] == "25"
    assert parsed[0]["locations"] == ["0-4", "6-10"]


@pytest.mark.parametrize("duplicate", [False, True])
def test_parse_message_info_fragments_and_emote_locations(duplicate):
    fragments = [
        {"text": "Kappa", "emote": {"emoteID": "25", "id": "emote;0;4"}},
        {"text": " " if duplicate else " hi"},
    ]
    if duplicate:
        fragments.append(
            {"text": "Kappa", "emote": {"emoteID": "25", "id": "emote;6;10"}}
        )
    parsed = tw_messages._parse_message_info(
        gql_message(userColor="#abcdef", userBadges=[], fragments=fragments)
    )
    assert parsed["author_colour"] == "#abcdef"
    assert parsed["message"] == ("Kappa Kappa" if duplicate else "Kappa hi")
    assert parsed["emotes"] == [
        {
            "id": "25",
            "images": tw_messages._generate_emote_image_list("25"),
            "name": "Kappa",
            "locations": "0-4,6-10" if duplicate else "0-4",
        }
    ]


@pytest.mark.parametrize(
    ("name", "version", "title", "prefix"),
    [("subscriber", "12", "Sub", "s"), ("moderator", "1", "Mod", "g")],
)
def test_parse_badge_info_prefers_subscriber_over_global(name, version, title, prefix):
    badge_set = BadgeSet(
        global_badges={("moderator", "1"): badge_record()},
        channel_badges={
            "123": {
                ("subscriber", "12"): badge_record(
                    "Sub", "s", clickAction="open", clickURL="https://example.com"
                )
            }
        },
    )
    parsed = tw_messages._parse_badge_info(
        name, version, channel_id="123", badge_set=badge_set
    )
    assert parsed["name"] == name
    assert parsed["version"] == int(version)
    assert parsed["title"] == title
    assert parsed["icons"][0]["url"] == f"{prefix}1"


def test_parse_author_images_user_and_game_helpers() -> None:
    images = tw_emotes._parse_author_images(
        "https://static-cdn.jtvnw.net/jtv_user_pictures/example-profile_image-300x300.png",
    )
    assert images[0]["width"] == 300
    assert images[1]["width"] == 70
    assert "70x70" in images[1]["url"]

    assert tw_messages._parse_user(None) == {}
    assert tw_messages._parse_user(
        {
            "id": "1",
            "login": "streamer",
            "displayName": "Streamer",
            "profileImageURL": "https://img.example/profile.png",
            "primaryColorHex": "#abcdef",
        },
    ) == {
        "id": "1",
        "name": "streamer",
        "display_name": "Streamer",
        "profile_image_url": "https://img.example/profile.png",
        "colour": "#abcdef",
    }

    assert tw_messages._parse_game(None) is None
    assert tw_messages._parse_game(
        {
            "id": "10",
            "name": "slug",
            "displayName": "Example Game",
            "boxArtURL": "https://img.example/game.jpg",
        },
    ) == {
        "id": "10",
        "name": "slug",
        "display_name": "Example Game",
        "box_art_url": "https://img.example/game.jpg",
    }


def test_parse_irc_badges_accepts_entries_without_version() -> None:
    badge_set = BadgeSet(global_badges={}, channel_badges={})

    from chat_downloader.sites.twitch.parsing.badges import _parse_irc_badges

    parsed = _parse_irc_badges("vip", "123", badge_set=badge_set)

    assert parsed == [{"name": "vip", "version": ""}]


def test_set_message_type_and_add_text_for_emotes_handle_unknown_and_invalid():
    info = {}
    tw_irc_resolve._set_message_type(info, "mystery_type")
    assert "message_type" not in info
    emotes = [{"locations": ["bad-location"]}]
    tw_emotes._add_text_for_emotes("hello", emotes)
    assert "name" not in emotes[0]


@pytest.mark.parametrize(
    ("kind", "debug"),
    [
        ("message-type", True),
        ("irc-action", True),
        ("irc-tag", True),
        ("irc-tag", False),
    ],
)
def test_parse_captures_unknown_payloads(monkeypatch, kind, debug):
    calls = []
    monkeypatch.setattr(tw_messages.logger, "isEnabledFor", lambda _level: debug)
    module = tw_messages if kind == "irc-tag" else tw_irc_resolve
    monkeypatch.setattr(
        module,
        "capture_debug_sample",
        lambda *args, **kwargs: calls.append((deepcopy(args), kwargs)),
    )
    if kind == "message-type":
        info = {}
        raw = {"message": {"messageType": "mystery_type"}}
        tw_irc_resolve._set_message_type(info, "mystery_type", raw_payload=raw)
        payload = {"raw": raw, "message_type": "mystery_type", "parsed": info}
    elif kind == "irc-action":
        raw = irc_frame(action="MYSTERY", user="")
        parsed = _parse_irc(raw)
        payload = {"raw": raw, "action_type": "MYSTERY", "parsed": parsed}
    else:
        raw = irc_frame("made-up-tag=value")
        assert _parse_irc(raw)["made_up_tag"] == "value"
        payload = {"raw": raw, "unknown_tags": ["made-up-tag"]}
    assert calls == (
        [((f"twitch-unknown-{kind}", payload), {"sample_limit": 10})] if debug else []
    )


def test_parse_item_defaults_to_text_message_and_drops_empty_badges() -> None:
    item = gql_comment(
        gql_message(userColor="#ffffff", userBadges=[{"setID": "subscriber"}]),
        commenter={
            "id": "42",
            "login": "streamer",
            "displayName": "Streamer",
            "profileImageURL": "https://img.example/profile.png",
            "primaryColorHex": "#abcdef",
        },
    )
    parsed = tw_messages._parse_item(item, offset=5.0, channel_id="123")

    assert parsed["message"] == "hello"
    assert parsed["message_type"] == "text_message"
    assert parsed["time_in_seconds"] == 10.0
    assert parsed["time_text"] == "0:10"
    assert "badges" not in parsed["author"]


def test_parse_item_accepts_mobile_emote_positions() -> None:
    parsed = tw_messages._parse_item(
        {
            "id": "mobile-msg",
            "message": {
                "userBadges": [],
                "fragments": [
                    {
                        "text": "Kappa hello",
                        "emote": {"from": 0, "emoteID": "25", "to": 4},
                    }
                ],
            },
        },
        offset=0,
    )

    assert parsed["message"] == "Kappa hello"
    assert parsed["emotes"][0]["id"] == "25"
    assert parsed["emotes"][0]["name"] == "Kappa"
    assert parsed["emotes"][0]["locations"] == "0-4"


def test_parse_item_attaches_badges_without_commenter() -> None:
    parsed = tw_messages._parse_item(
        gql_comment(
            gql_message(
                userBadges=[
                    None,
                    {"setID": "subscriber"},
                    {"setID": "moderator", "version": "1"},
                ]
            )
        ),
        offset=0,
        channel_id="123",
    )

    assert parsed["message"] == "hello"
    assert parsed["author"] == {"badges": [{"name": "moderator", "version": 1}]}


def test_parse_item_remaps_known_message_type(monkeypatch, request) -> None:
    item = gql_comment(gql_message(fragments=[]))

    monkeypatch.setattr(
        tw_messages,
        "_parse_message_info",
        lambda _message: {"message": "hello", "message_type": "announcement"},
    )
    build_comment_remapping.cache_clear()
    request.addfinalizer(build_comment_remapping.cache_clear)

    parsed = tw_messages._parse_item(item, offset=0.0, channel_id="123")

    assert parsed["message_type"] == "announcement"


def test_parse_irc_item_parses_emotes_subscriber_months_and_reply_author() -> None:
    parsed = _parse_irc(
        irc_frame(
            "badge-info=subscriber/12;badges=subscriber/12;emotes=25:0-4;subscriber=1;"
            "reply-parent-user-id=321;reply-parent-msg-id=parent-msg;"
            "reply-parent-display-name=OtherUser;reply-parent-user-login=otheruser",
            text="Kappa",
        )
    )

    assert parsed["message"] == "Kappa"
    assert parsed["emotes"][0]["name"] == "Kappa"
    assert parsed["author"]["badges"][0]["months"] == 12
    assert parsed["in_reply_to"]["author"]["name"] == "otheruser"
    assert parsed["author"]["name"] == "testuser"


@pytest.mark.parametrize(
    ("kind", "tags", "text", "user"),
    [
        (
            "animated-message",
            "animation-id=party;badges=vip/1",
            "hello",
            "animateduser",
        ),
        (
            "gigantified-emote-message",
            "emotes=25:0-4;badges=subscriber/12",
            "Kappa",
            "giganticuser",
        ),
    ],
)
def test_parse_special_message_without_unknown_warning(
    monkeypatch, kind, tags, text, user
):
    debug_calls = []
    monkeypatch.setattr(
        tw_irc_resolve, "debug_log", lambda *args: debug_calls.append(args)
    )
    parsed = _parse_irc(
        irc_frame(f"{tags};msg-id={kind};display-name={user}", text=text, user=user)
    )
    assert parsed["action_type"] == "text_message"
    assert parsed["message_type"] == kind
    assert parsed["message"] == text
    assert parsed["author"]["name"] == user
    if kind == "animated-message":
        assert parsed["animation_id"] == "party"
    else:
        assert parsed["emotes"][0]["id"] == "25"
        assert parsed["emotes"][0]["name"] == "Kappa"
        assert parsed["channel_id"] == "999"
        assert parsed["author"]["badges"][0]["name"] == "subscriber"
    assert not any(
        "Unknown message type" in str(call[0]) for call in debug_calls if call
    )


def test_parse_irc_item_preserves_mystery_gift_theme() -> None:
    parsed = _parse_irc(
        irc_frame(
            "msg-id=submysterygift;msg-param-mass-gift-count=5;msg-param-gift-theme=hype",
            "USERNOTICE",
            None,
            user="giftuser",
        )
    )

    assert parsed["action_type"] == "user_notice"
    assert parsed["message_type"] == "mystery_subscription_gift"
    assert parsed["mass_gift_count"] == 5
    assert parsed["msg_param_gift_theme"] == "hype"


def test_parse_irc_item_handles_unknown_action_roomstate_and_clearchat(
    monkeypatch,
) -> None:
    debug_calls = []
    monkeypatch.setattr(
        tw_irc_resolve,
        "debug_log",
        lambda *items: debug_calls.append(items),
    )

    unknown_raw = (
        "@badge-info=;badges=;display-name=TestUser;room-id=999;tmi-sent-ts=1;user-id=12345 "  # noqa: E501
        ":tmi.twitch.tv MYSTERY #channel :hello\r\n"
    )
    roomstate_raw = (
        "@badge-info=;badges=;display-name=TestUser;followers-only=10;room-id=999;"
        "slow=5;tmi-sent-ts=1;user-id=12345 :tmi.twitch.tv ROOMSTATE #channel\r\n"
    )
    clear_timeout_raw = (
        "@ban-duration=600;room-id=999;target-user-id=200;tmi-sent-ts=1 "
        ":tmi.twitch.tv CLEARCHAT #channel :banneduser\r\n"
    )
    clear_chat_raw = "@room-id=999;tmi-sent-ts=1 :tmi.twitch.tv CLEARCHAT #channel\r\n"
    unknown = _parse_irc(unknown_raw)
    roomstate = _parse_irc(roomstate_raw)
    clear_timeout = _parse_irc(clear_timeout_raw)
    clear_chat = _parse_irc(clear_chat_raw)

    assert unknown["action_type"] == "MYSTERY"
    assert unknown["message_type"] == "MYSTERY"
    assert debug_calls[0][0] == [
        "Unknown action type: MYSTERY",
        "MYSTERY",
        unknown,
    ]

    assert roomstate["action_type"] == "room_state"
    assert roomstate["message_type"] == "room_state"
    assert roomstate["follower_only"] is True
    assert roomstate["minutes_to_follow_before_chatting"] == 10
    assert roomstate["slow_mode"] is True
    assert roomstate["seconds_to_wait"] == 5

    assert clear_timeout["message_type"] == "ban_user"
    assert clear_timeout["ban_type"] == "timeout"
    assert clear_timeout["banned_user"] == "banneduser"
    assert "message" not in clear_timeout
    assert clear_chat["message_type"] == "clear_chat"


def test_parse_irc_item_handles_flag_without_equals_and_disabled_modes() -> None:
    raw = (
        "@vip;badge-info=;badges=;display-name=TestUser;followers-only=-1;room-id=999;"
        "slow=0;tmi-sent-ts=1;user-id=12345 :tmi.twitch.tv ROOMSTATE #channel\r\n"
    )

    parsed = _parse_irc(raw)

    assert parsed["is_vip"] is True
    assert parsed["follower_only"] is False
    assert "minutes_to_follow_before_chatting" not in parsed
    assert parsed["slow_mode"] is False


def test_parse_message_info_skips_malformed_vod_emote_and_keeps_message_text() -> None:
    """A malformed emote id in a VOD fragment must not crash the parse."""
    message = {
        "userColor": "#abcdef",
        "userBadges": [],
        "fragments": [
            {"text": "hello "},
            {
                "text": "BadEmote",
                "emote": {"emoteID": "999", "id": "no-semicolons-here"},
            },
            {"text": " world"},
        ],
    }
    parsed = tw_messages._parse_message_info(message)
    assert parsed["message"] == "hello BadEmote world"
    assert "emotes" not in parsed


def test_parse_irc_item_follower_only_unexpected_negative_treated_as_disabled() -> None:
    """A negative follower_only other than -1 must not set it True."""
    raw = (
        "@badge-info=;badges=;display-name=TestUser;followers-only=-2;room-id=999;"
        "tmi-sent-ts=1;user-id=12345 :tmi.twitch.tv ROOMSTATE #channel\r\n"
    )
    parsed = _parse_irc(raw)
    assert parsed["follower_only"] is False
    assert "minutes_to_follow_before_chatting" not in parsed


@pytest.mark.parametrize(
    ("info", "badge_key", "expected", "consumed"),
    [
        (
            {"author_badge_metadata": "subscriber/6", "author_badges": "subscriber/1"},
            "author_badges",
            {"name": "subscriber", "version": 1, "months": 6},
            "author_badge_metadata",
        ),
        (
            {
                "author_badge_metadata": "",
                "author_badges": "vip/1",
                "shared_chat_source_channel_id": "456",
                "shared_chat_source_badges": "subscriber/3",
                "shared_chat_source_badge_info": "subscriber/24",
            },
            "shared_chat_source_badges",
            {"name": "subscriber", "version": 3, "months": 24},
            "shared_chat_source_badge_info",
        ),
    ],
    ids=["author-months", "shared-chat-months"],
)
def test_resolve_irc_badges_enriches_and_consumes_metadata(
    info,
    badge_key,
    expected,
    consumed,
) -> None:
    info = deepcopy(info)
    tw_messages._resolve_irc_badges(info, channel_id="123", badge_set=BadgeSet({}, {}))
    assert info[badge_key] == [expected]
    assert consumed not in info


def test_resolve_irc_badges_absent_source_stays_absent() -> None:
    info = {"author_badge_metadata": "", "author_badges": "moderator/1"}
    tw_messages._resolve_irc_badges(info, channel_id="100", badge_set=BadgeSet({}, {}))
    assert "shared_chat_source_badges" not in info
