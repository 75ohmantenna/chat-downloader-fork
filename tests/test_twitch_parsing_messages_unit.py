# SPDX-License-Identifier: MIT

from __future__ import annotations

from chat_downloader.sites.twitch.constants import (
    MESSAGE_GROUPS,
    MESSAGE_REGEX,
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
from chat_downloader.sites.twitch.parsing.messages import _parse_irc_item
from chat_downloader.sites.twitch.parsing.tag_decoding import (
    _decode_pseudo_bnf,
    _parse_bool,
    _parse_bool_text,
)
from chat_downloader.sites.twitch.remappings import build_comment_remapping
from chat_downloader.sites.twitch.types import BadgeSet


def test_announcement_is_mapped_and_in_default_message_group() -> None:
    assert MESSAGE_TYPE_REMAPPING["announcement"] == "announcement"
    assert "announcement" in MESSAGE_GROUPS["messages"]


def test_new_text_variants_are_mapped_and_in_default_message_group() -> None:
    assert MESSAGE_TYPE_REMAPPING["animated-message"] == "animated-message"
    assert (
        MESSAGE_TYPE_REMAPPING["gigantified-emote-message"]
        == "gigantified-emote-message"
    )
    assert "animated-message" in MESSAGE_GROUPS["messages"]
    assert "gigantified-emote-message" in MESSAGE_GROUPS["messages"]


def test_sharedchatnotice_is_mapped_and_in_notices_group() -> None:
    assert MESSAGE_TYPE_REMAPPING["sharedchatnotice"] == "shared_chat_notice"
    assert "shared_chat_notice" in MESSAGE_GROUPS["other"]


def test_parse_irc_item_parses_announcement_usernotice() -> None:
    raw = (
        "@badge-info=;badges=moderator/1,partner/1;color=#5B99FF;"
        "display-name=StreamElements;emotes=;flags=;"
        "id=db229975-40e4-4d55-92de-75ec0e8b3cb2;mod=1;room-id=93869876;"
        "subscriber=0;tmi-sent-ts=1771953482608;turbo=0;user-id=100135110;"
        "user-type=mod;msg-id=announcement;msg-param-color=PRIMARY;"
        "system-msg= :tmi.twitch.tv USERNOTICE #thebausffs "
        ":DinkDonk GAMBA BET YOUR POINTS Shirley YOU WILL WIN THIS TIME DESPAIR\r\n"
    )

    match = MESSAGE_REGEX.search(raw)
    assert match is not None

    parsed = _parse_irc_item(match)

    assert parsed["action_type"] == "user_notice"
    assert parsed["message_type"] == "announcement"
    assert parsed["announcement_colour"] == "PRIMARY"
    assert parsed["message"] == (
        "DinkDonk GAMBA BET YOUR POINTS Shirley YOU WILL WIN THIS TIME DESPAIR"
    )
    assert parsed["author"]["name"] == "streamelements"


def test_parse_irc_item_parses_shared_chat_privmsg_tags() -> None:
    raw = (
        "@badge-info=;badges=vip/1;color=#1E90FF;display-name=GuestUser;emotes=;"
        "flags=;id=22fe4db9-1f83-4d8e-b4b9-d9f840d5f001;mod=0;room-id=123;"
        "source-id=shared-message-1;source-room-id=456;source-badges=moderator/1;"
        "source-badge-info=subscriber/12;source-only=1;subscriber=0;"
        "tmi-sent-ts=1771953482608;turbo=0;user-id=789;user-type= "
        ":guestuser!guestuser@guestuser.tmi.twitch.tv PRIVMSG #example :hello\r\n"
    )

    match = MESSAGE_REGEX.search(raw)
    assert match is not None

    parsed = _parse_irc_item(match)

    assert parsed["shared_chat_source_message_id"] == "shared-message-1"
    assert parsed["shared_chat_source_channel_id"] == "456"
    assert parsed["shared_chat_source_badges"][0]["name"] == "moderator"
    assert parsed["shared_chat_source_badges"][0]["version"] == 1
    assert parsed["shared_chat_source_only"] is True
    assert parsed["is_shared_chat_message"] is True
    assert parsed["shared_chat_effective_source_channel_id"] == "456"
    assert parsed["shared_chat_is_cross_channel"] is True


def test_parse_irc_item_applies_shared_chat_subscriber_badge_metadata() -> None:
    raw = (
        "@badge-info=;badges=vip/1;color=#1E90FF;display-name=GuestUser;emotes=;"
        "flags=;id=22fe4db9-1f83-4d8e-b4b9-d9f840d5f001;mod=0;room-id=123;"
        "source-id=shared-message-1;source-room-id=456;source-badges=subscriber/1;"
        "source-badge-info=subscriber/12;source-only=1;subscriber=0;"
        "tmi-sent-ts=1771953482608;turbo=0;user-id=789;user-type= "
        ":guestuser!guestuser@guestuser.tmi.twitch.tv PRIVMSG #example :hello\r\n"
    )

    match = MESSAGE_REGEX.search(raw)
    assert match is not None

    parsed = _parse_irc_item(match)

    assert parsed["shared_chat_source_badges"][0]["name"] == "subscriber"
    assert parsed["shared_chat_source_badges"][0]["version"] == 1
    assert parsed["shared_chat_source_badges"][0]["months"] == 12


def test_parse_irc_item_parses_shared_chat_usernotice_source_msg_id() -> None:
    raw = (
        "@badge-info=;badges=moderator/1;color=#5B99FF;display-name=Fossabot;"
        "emotes=;flags=;id=db229975-40e4-4d55-92de-75ec0e8b3cb2;mod=1;"
        "room-id=93869876;source-msg-id=announcement;subscriber=0;"
        "tmi-sent-ts=1771953482608;turbo=0;user-id=100135110;user-type=mod;"
        "msg-id=announcement;msg-param-color=PRIMARY;system-msg= "
        ":tmi.twitch.tv USERNOTICE #thebausffs :promo\r\n"
    )

    match = MESSAGE_REGEX.search(raw)
    assert match is not None

    parsed = _parse_irc_item(match)

    assert parsed["message_type"] == "announcement"
    assert parsed["shared_chat_source_msg_id"] == "announcement"


def test_parse_irc_item_parses_sharedchatnotice_usernotice() -> None:
    raw = (
        "@badge-info=;badges=moderator/1;color=#5B99FF;display-name=Fossabot;"
        "emotes=;flags=;id=db229975-40e4-4d55-92de-75ec0e8b3cb2;mod=1;"
        "room-id=93869876;source-id=shared-message-1;source-room-id=123456;"
        "source-msg-id=announcement;source-only=1;subscriber=0;"
        "tmi-sent-ts=1771953482608;turbo=0;user-id=100135110;user-type=mod;"
        "msg-id=sharedchatnotice;system-msg=Shared\\sChat\\snotice "
        ":tmi.twitch.tv USERNOTICE #thebausffs :promo\r\n"
    )

    match = MESSAGE_REGEX.search(raw)
    assert match is not None

    parsed = _parse_irc_item(match)

    assert parsed["action_type"] == "user_notice"
    assert parsed["message_type"] == "shared_chat_notice"
    assert parsed["system_message"] == "Shared Chat notice"
    assert parsed["shared_chat_source_message_id"] == "shared-message-1"
    assert parsed["shared_chat_source_channel_id"] == "123456"
    assert parsed["shared_chat_source_msg_id"] == "announcement"
    assert parsed["shared_chat_source_only"] is True
    assert parsed["is_shared_chat_message"] is True
    assert parsed["shared_chat_effective_source_channel_id"] == "123456"
    assert parsed["shared_chat_is_cross_channel"] is True


def test_parse_irc_item_preserves_sharedchatnotice_goal_params() -> None:
    raw = (
        "@badge-info=;badges=moderator/1;color=#5B99FF;display-name=Fossabot;"
        "emotes=;flags=;id=db229975-40e4-4d55-92de-75ec0e8b3cb2;mod=1;"
        "room-id=93869876;source-id=shared-message-1;source-room-id=123456;"
        "source-msg-id=announcement;subscriber=0;tmi-sent-ts=1771953482608;"
        "turbo=0;user-id=100135110;user-type=mod;msg-id=sharedchatnotice;"
        "msg-param-goal-target-contributions=100;"
        "msg-param-goal-current-contributions=25;"
        "msg-param-goal-user-contributions=5;"
        "msg-param-goal-description=Daily\\sgoal;"
        "msg-param-goal-contribution-type=BITS;system-msg=Shared\\sChat "
        ":tmi.twitch.tv USERNOTICE #thebausffs :promo\r\n"
    )

    match = MESSAGE_REGEX.search(raw)
    assert match is not None

    parsed = _parse_irc_item(match)

    assert parsed["message_type"] == "shared_chat_notice"
    assert parsed["msg_param_goal_target_contributions"] == "100"
    assert parsed["msg_param_goal_current_contributions"] == "25"
    assert parsed["msg_param_goal_user_contributions"] == "5"
    assert parsed["msg_param_goal_description"] == r"Daily\sgoal"
    assert parsed["msg_param_goal_contribution_type"] == "BITS"


def test_parse_irc_item_sets_shared_chat_fields_for_same_channel_source() -> None:
    raw = (
        "@badge-info=;badges=vip/1;color=#1E90FF;display-name=GuestUser;emotes=;"
        "flags=;id=22fe4db9-1f83-4d8e-b4b9-d9f840d5f001;mod=0;room-id=123;"
        "source-id=shared-message-1;source-room-id=123;subscriber=0;"
        "tmi-sent-ts=1771953482608;turbo=0;user-id=789;user-type= "
        ":guestuser!guestuser@guestuser.tmi.twitch.tv PRIVMSG #example :hello\r\n"
    )

    match = MESSAGE_REGEX.search(raw)
    assert match is not None

    parsed = _parse_irc_item(match)

    assert parsed["is_shared_chat_message"] is True
    assert parsed["shared_chat_effective_source_channel_id"] == "123"
    assert parsed["shared_chat_is_cross_channel"] is False


def test_decode_pseudo_bnf() -> None:
    assert _decode_pseudo_bnf(r"hello\sworld\:\:") == "hello world;;"


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


def test_parse_message_info_fragments_and_emotes() -> None:
    message = {
        "userColor": "#abcdef",
        "userBadges": [],
        "fragments": [
            {
                "text": "Kappa",
                "emote": {"emoteID": "25", "id": "ignored;0;4"},
            },
            {"text": " hi"},
        ],
    }
    parsed = tw_messages._parse_message_info(message)
    assert parsed["author_colour"] == "#abcdef"
    assert parsed["message"] == "Kappa hi"
    assert parsed["emotes"][0]["id"] == "25"
    assert parsed["emotes"][0]["name"] == "Kappa"
    assert parsed["emotes"][0]["locations"] == "0-4"


def test_parse_message_info_merges_duplicate_emote_locations() -> None:
    message = {
        "userColor": "#fff",
        "userBadges": [],
        "fragments": [
            {
                "text": "Kappa",
                "emote": {"emoteID": "25", "id": "emote;0;4"},
            },
            {"text": " "},
            {
                "text": "Kappa",
                "emote": {"emoteID": "25", "id": "emote;6;10"},
            },
        ],
    }

    parsed = tw_messages._parse_message_info(message)

    assert parsed["message"] == "Kappa Kappa"
    assert parsed["emotes"] == [
        {
            "id": "25",
            "images": tw_messages._generate_emote_image_list("25"),
            "name": "Kappa",
            "locations": "0-4,6-10",
        },
    ]


def test_parse_badge_info_prefers_subscriber_over_global() -> None:
    badge_set = BadgeSet(
        global_badges={
            ("moderator", "1"): {
                "title": "Mod",
                "image1x": "g1",
                "image2x": "g2",
                "image4x": "g4",
                "clickAction": None,
                "clickURL": None,
            },
        },
        channel_badges={
            "123": {
                ("subscriber", "12"): {
                    "title": "Sub",
                    "image1x": "s1",
                    "image2x": "s2",
                    "image4x": "s4",
                    "clickAction": "open",
                    "clickURL": "https://example.com",
                },
            },
        },
    )

    sub = tw_messages._parse_badge_info(
        "subscriber",
        "12",
        channel_id="123",
        badge_set=badge_set,
    )
    assert sub["name"] == "subscriber"
    assert sub["version"] == 12
    assert sub["title"] == "Sub"
    assert sub["icons"][0]["url"] == "s1"

    mod = tw_messages._parse_badge_info(
        "moderator",
        "1",
        channel_id="123",
        badge_set=badge_set,
    )
    assert mod["name"] == "moderator"
    assert mod["version"] == 1
    assert mod["title"] == "Mod"
    assert mod["icons"][0]["url"] == "g1"


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


def test_set_message_type_and_add_text_for_emotes_handle_unknown_and_invalid(
    monkeypatch,
) -> None:
    debug_calls = []
    info: dict[str, object] = {}

    monkeypatch.setattr(
        tw_irc_resolve,
        "debug_log",
        lambda *items: debug_calls.append(items),
    )
    monkeypatch.setattr(
        tw_emotes,
        "debug_log",
        lambda *items: debug_calls.append(items),
    )

    tw_irc_resolve._set_message_type(info, "mystery_type")
    assert "message_type" not in info
    assert debug_calls == [("Unknown message type: mystery_type", "Parsed data: {}")]

    emotes = [{"locations": ["bad-location"]}]
    tw_emotes._add_text_for_emotes("hello", emotes)
    assert "name" not in emotes[0]
    assert debug_calls[-1] == (
        "Invalid emote: {'locations': ['bad-location']}",
        "Message: hello",
    )


def test_parse_item_defaults_to_text_message_and_drops_empty_badges() -> None:
    item = {
        "id": "msg-1",
        "createdAt": "2024-01-01T00:00:01Z",
        "contentOffsetSeconds": 15,
        "commenter": {
            "id": "42",
            "login": "streamer",
            "displayName": "Streamer",
            "profileImageURL": "https://img.example/profile.png",
            "primaryColorHex": "#abcdef",
        },
        "message": {
            "userColor": "#ffffff",
            "userBadges": [{"setID": "subscriber"}],
            "fragments": [{"text": "hello"}],
        },
    }

    parsed = tw_messages._parse_item(item, offset=5.0, channel_id="123")

    assert parsed["message"] == "hello"
    assert parsed["message_type"] == "text_message"
    assert parsed["time_in_seconds"] == 10.0
    assert parsed["time_text"] == "0:10"
    assert "badges" not in parsed["author"]


def test_parse_item_remaps_known_message_type(monkeypatch, request) -> None:
    item = {
        "id": "msg-2",
        "createdAt": "2024-01-01T00:00:02Z",
        "contentOffsetSeconds": 2,
        "commenter": {
            "id": "42",
            "login": "streamer",
            "displayName": "Streamer",
            "profileImageURL": "https://img.example/profile.png",
            "primaryColorHex": "#abcdef",
        },
        "message": {"fragments": []},
    }

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
    raw = (
        "@badge-info=subscriber/12;badges=subscriber/12;color=#00FF00;"
        "display-name=TestUser;emotes=25:0-4;flags=;id=abc123;mod=0;room-id=999;"
        "reply-parent-user-id=321;reply-parent-msg-id=parent-msg;"
        "reply-parent-display-name=OtherUser;reply-parent-user-login=otheruser;"
        "subscriber=1;tmi-sent-ts=1700000000000;turbo=0;user-id=12345;user-type= "
        ":testuser!testuser@testuser.tmi.twitch.tv PRIVMSG #channel :Kappa"
        "\r\n"
    )

    match = MESSAGE_REGEX.search(raw)
    assert match is not None

    parsed = _parse_irc_item(match)

    assert parsed["message"] == "Kappa"
    assert parsed["emotes"][0]["name"] == "Kappa"
    assert parsed["author"]["badges"][0]["months"] == 12
    assert parsed["in_reply_to"]["author"]["name"] == "otheruser"
    assert parsed["author"]["name"] == "testuser"


def test_parse_irc_item_parses_animated_message_without_unknown_warning(
    monkeypatch,
) -> None:
    debug_calls = []
    monkeypatch.setattr(
        tw_irc_resolve,
        "debug_log",
        lambda *items: debug_calls.append(items),
    )
    raw = (
        "@badge-info=;badges=vip/1;color=#1E90FF;display-name=AnimatedUser;"
        "emotes=;flags=;id=animated-msg-1;mod=0;room-id=999;"
        "animation-id=party;subscriber=0;tmi-sent-ts=1700000000000;turbo=0;"
        "user-id=12345;user-type=;msg-id=animated-message "
        ":animateduser!animateduser@animateduser.tmi.twitch.tv PRIVMSG "
        "#channel :hello\r\n"
    )

    match = MESSAGE_REGEX.search(raw)
    assert match is not None

    parsed = _parse_irc_item(match)

    assert parsed["action_type"] == "text_message"
    assert parsed["message_type"] == "animated-message"
    assert parsed["animation_id"] == "party"
    assert parsed["message"] == "hello"
    assert parsed["author"]["name"] == "animateduser"
    assert not any(
        call and "Unknown message type" in str(call[0]) for call in debug_calls
    )


def test_parse_irc_item_parses_gigantified_emote_without_unknown_warning(
    monkeypatch,
) -> None:
    debug_calls = []
    monkeypatch.setattr(
        tw_irc_resolve,
        "debug_log",
        lambda *items: debug_calls.append(items),
    )
    raw = (
        "@badge-info=;badges=subscriber/12;color=#00FF00;"
        "display-name=GiganticUser;emotes=25:0-4;flags=;id=gigantic-msg-1;"
        "mod=0;room-id=999;subscriber=1;tmi-sent-ts=1700000000000;turbo=0;"
        "user-id=12345;user-type=;msg-id=gigantified-emote-message "
        ":giganticuser!giganticuser@giganticuser.tmi.twitch.tv PRIVMSG "
        "#channel :Kappa\r\n"
    )

    match = MESSAGE_REGEX.search(raw)
    assert match is not None

    parsed = _parse_irc_item(match)

    assert parsed["action_type"] == "text_message"
    assert parsed["message_type"] == "gigantified-emote-message"
    assert parsed["message"] == "Kappa"
    assert parsed["emotes"][0]["id"] == "25"
    assert parsed["emotes"][0]["name"] == "Kappa"
    assert parsed["channel_id"] == "999"
    assert parsed["author"]["badges"][0]["name"] == "subscriber"
    assert not any(
        call and "Unknown message type" in str(call[0]) for call in debug_calls
    )


def test_parse_irc_item_preserves_mystery_gift_theme() -> None:
    raw = (
        "@badge-info=;badges=;color=#9146FF;display-name=GiftUser;emotes=;"
        "flags=;id=gift-msg-1;mod=0;room-id=999;subscriber=0;"
        "tmi-sent-ts=1700000000000;turbo=0;user-id=12345;user-type=;"
        "msg-id=submysterygift;msg-param-mass-gift-count=5;"
        "msg-param-gift-theme=hype "
        ":giftuser!giftuser@giftuser.tmi.twitch.tv USERNOTICE #channel\r\n"
    )

    match = MESSAGE_REGEX.search(raw)
    assert match is not None

    parsed = _parse_irc_item(match)

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

    unknown_match = MESSAGE_REGEX.search(unknown_raw)
    roomstate_match = MESSAGE_REGEX.search(roomstate_raw)
    clear_timeout_match = MESSAGE_REGEX.search(clear_timeout_raw)
    clear_chat_match = MESSAGE_REGEX.search(clear_chat_raw)

    assert unknown_match is not None
    assert roomstate_match is not None
    assert clear_timeout_match is not None
    assert clear_chat_match is not None

    unknown = _parse_irc_item(unknown_match)
    roomstate = _parse_irc_item(roomstate_match)
    clear_timeout = _parse_irc_item(clear_timeout_match)
    clear_chat = _parse_irc_item(clear_chat_match)

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
    assert clear_chat["message_type"] == "clear_chat"


def test_parse_irc_item_handles_flag_without_equals_and_disabled_modes() -> None:
    raw = (
        "@vip;badge-info=;badges=;display-name=TestUser;followers-only=-1;room-id=999;"
        "slow=0;tmi-sent-ts=1;user-id=12345 :tmi.twitch.tv ROOMSTATE #channel\r\n"
    )

    match = MESSAGE_REGEX.search(raw)
    assert match is not None

    parsed = _parse_irc_item(match)

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


def test_decode_pseudo_bnf_converts_backslash_escape() -> None:
    assert _decode_pseudo_bnf(r"a\\b") == r"a\b"


def test_parse_irc_item_follower_only_unexpected_negative_treated_as_disabled() -> None:
    """A negative follower_only other than -1 must not set it True."""
    raw = (
        "@badge-info=;badges=;display-name=TestUser;followers-only=-2;room-id=999;"
        "tmi-sent-ts=1;user-id=12345 :tmi.twitch.tv ROOMSTATE #channel\r\n"
    )
    match = MESSAGE_REGEX.search(raw)
    assert match is not None
    parsed = _parse_irc_item(match)
    assert parsed["follower_only"] is False
    assert "minutes_to_follow_before_chatting" not in parsed


# ---------------------------------------------------------------------------
# Isolation tests for extracted helper: _resolve_irc_badges
# ---------------------------------------------------------------------------


def test_resolve_irc_badges_sets_author_badges_and_applies_subscriber_months() -> None:
    """_resolve_irc_badges parses main badges and applies badge-info."""
    from chat_downloader.sites.twitch.parsing.messages import (
        _resolve_irc_badges,
    )

    info: dict = {
        "author_badge_metadata": "subscriber/6",
        "author_badges": "subscriber/1",
        "channel_id": "999",
    }
    badge_set = BadgeSet(global_badges={}, channel_badges={})

    _resolve_irc_badges(info, channel_id="999", badge_set=badge_set)

    assert info["author_badges"][0]["name"] == "subscriber"
    assert info["author_badges"][0]["version"] == 1
    assert info["author_badges"][0]["months"] == 6
    # raw string keys must be consumed
    assert "author_badge_metadata" not in info


def test_resolve_irc_badges_shared_chat_badge_enrichment() -> None:
    """Shared-chat source badges are parsed and subscriber months applied."""
    from chat_downloader.sites.twitch.parsing.messages import (
        _resolve_irc_badges,
    )

    info: dict = {
        "author_badge_metadata": "",
        "author_badges": "vip/1",
        "channel_id": "123",
        "shared_chat_source_channel_id": "456",
        "shared_chat_source_badges": "subscriber/3",
        "shared_chat_source_badge_info": "subscriber/24",
    }
    badge_set = BadgeSet(global_badges={}, channel_badges={})

    _resolve_irc_badges(info, channel_id="123", badge_set=badge_set)

    assert info["shared_chat_source_badges"][0]["name"] == "subscriber"
    assert info["shared_chat_source_badges"][0]["months"] == 24
    # raw info key must be consumed
    assert "shared_chat_source_badge_info" not in info


def test_resolve_irc_badges_absent_shared_chat_source_badges_leaves_key_absent() -> (
    None
):
    """When shared_chat_source_badges is empty, the key is absent."""
    from chat_downloader.sites.twitch.parsing.messages import (
        _resolve_irc_badges,
    )

    info: dict = {
        "author_badge_metadata": "",
        "author_badges": "moderator/1",
        "channel_id": "100",
    }
    badge_set = BadgeSet(global_badges={}, channel_badges={})

    _resolve_irc_badges(info, channel_id="100", badge_set=badge_set)

    assert "shared_chat_source_badges" not in info


# ---------------------------------------------------------------------------
# Isolation tests for extracted helper: _resolve_irc_action_and_message_type
# ---------------------------------------------------------------------------


def test_resolve_irc_action_known_action_and_message_type() -> None:
    """A known action type and known message type must both be remapped."""
    from chat_downloader.sites.twitch.parsing.messages import (
        _resolve_irc_action_and_message_type,
    )

    info: dict = {"message_type": "announcement"}
    _resolve_irc_action_and_message_type(info, "USERNOTICE", None)

    assert info["action_type"] == "user_notice"
    assert info["message_type"] == "announcement"


def test_resolve_irc_action_unknown_action_falls_back_to_raw_and_logs(
    monkeypatch,
) -> None:
    """An unknown action type must be stored verbatim and trigger debug_log."""
    import chat_downloader.sites.twitch.parsing.message_irc_resolve as _mod
    from chat_downloader.sites.twitch.parsing.message_irc_resolve import (
        _resolve_irc_action_and_message_type,
    )

    calls: list = []
    monkeypatch.setattr(_mod, "debug_log", lambda *a: calls.append(a))

    info: dict = {}
    _resolve_irc_action_and_message_type(info, "MYSTERY", None)

    assert info["action_type"] == "MYSTERY"
    assert info["message_type"] == "MYSTERY"
    assert calls  # debug_log was called


def test_resolve_irc_action_clearchat_with_message_is_ban_timeout() -> None:
    """CLEARCHAT with a message match yields a ban_user/timeout outcome."""
    from chat_downloader.sites.twitch.parsing.messages import (
        _resolve_irc_action_and_message_type,
    )

    info: dict = {"ban_duration": "600", "message": "banneduser"}
    _resolve_irc_action_and_message_type(info, "CLEARCHAT", "banneduser")

    assert info["message_type"] == "ban_user"
    assert info["ban_type"] == "timeout"
    assert info["banned_user"] == "banneduser"
    assert "message" not in info


def test_resolve_irc_action_clearchat_without_message_is_clear_chat() -> None:
    """CLEARCHAT without a message match must map to clear_chat message type."""
    from chat_downloader.sites.twitch.parsing.messages import (
        _resolve_irc_action_and_message_type,
    )

    info: dict = {}
    _resolve_irc_action_and_message_type(info, "CLEARCHAT", None)

    assert info["message_type"] == "clear_chat"


def test_resolve_irc_action_follower_only_and_slow_mode_normalization() -> None:
    """follower_only and slow_mode must be normalized to bool + extra fields."""
    from chat_downloader.sites.twitch.parsing.messages import (
        _resolve_irc_action_and_message_type,
    )

    info: dict = {"follower_only": "10", "slow_mode": "5"}
    _resolve_irc_action_and_message_type(info, "ROOMSTATE", None)

    assert info["follower_only"] is True
    assert info["minutes_to_follow_before_chatting"] == 10
    assert info["slow_mode"] is True
    assert info["seconds_to_wait"] == 5


def test_resolve_irc_action_follower_only_disabled_and_slow_mode_off() -> None:
    """follower_only=-1 must yield False; slow_mode=0 must yield False."""
    from chat_downloader.sites.twitch.parsing.messages import (
        _resolve_irc_action_and_message_type,
    )

    info: dict = {"follower_only": "-1", "slow_mode": "0"}
    _resolve_irc_action_and_message_type(info, "ROOMSTATE", None)

    assert info["follower_only"] is False
    assert "minutes_to_follow_before_chatting" not in info
    assert info["slow_mode"] is False
