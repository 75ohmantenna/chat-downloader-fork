# SPDX-License-Identifier: MIT

from collections.abc import Mapping

import pytest

import chat_downloader.debugging as dbg
from chat_downloader.sites.youtube.constants_actions_messages_core import (
    _KNOWN_ACTION_TYPES,
)
from chat_downloader.sites.youtube.constants_message import (
    build_video_remapping,
)
from chat_downloader.sites.youtube.parsing.actions_handlers import (
    validate_and_finalize_message,
)
from chat_downloader.sites.youtube.parsing.actions_router import process_action


def _renderer_with_timestamp(usec: str = "1234567890") -> dict:
    # timestampUsec is a known remapping key and avoids "empty parse" debug
    # logging.
    return {"timestampUsec": usec}


def _finalize(result):
    assert result is not None
    data, original_item, original_message_type, original_action_type = result
    return validate_and_finalize_message(
        data,
        original_item,
        original_message_type,
        original_action_type,
    )


def setup_module() -> None:
    # Ensure debug_log never escalates to TestingException during unit tests.
    dbg.set_testing_mode(dbg.TestingModes.NONE)


def test_build_video_remapping_returns_mapping_with_expected_keys() -> None:
    """build_video_remapping() lazy-imports and returns the mapping."""
    mapping = build_video_remapping()
    assert isinstance(mapping, Mapping)
    assert "videoId" in mapping
    assert "title" in mapping


def test_process_action_replay_chat_item_action_rebases_time_and_action() -> (
    None
):
    action = {
        "replayChatItemAction": {
            "videoOffsetTimeMsec": "2345",
            "actions": [
                {
                    "addChatItemAction": {
                        "item": {
                            "liveChatTextMessageRenderer": _renderer_with_timestamp(),  # noqa: E501
                        },
                    },
                },
            ],
        },
    }

    finalized = _finalize(process_action(action))
    assert finalized is not None
    assert finalized["action_type"] == "add_chat_item"
    assert finalized["message_type"] == "text_message"
    assert finalized["time_in_seconds"] == pytest.approx(2.345)
    assert finalized["timestamp"] == 1234567890


def test_process_action_add_chat_item_action() -> None:
    action = {
        "addChatItemAction": {
            "item": {
                "liveChatTextMessageRenderer": _renderer_with_timestamp("1")
            },
        },
    }

    finalized = _finalize(process_action(action))
    assert finalized is not None
    assert finalized["action_type"] == "add_chat_item"
    assert finalized["message_type"] == "text_message"
    assert finalized["timestamp"] == 1


def test_process_action_gift_message_view_model(monkeypatch) -> None:
    logs = []
    samples = []
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.actions_handlers_validation.debug_log",
        lambda *parts: logs.append(parts),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.actions_handlers_validation.capture_debug_sample",
        lambda *parts: samples.append(parts),
    )

    action = {
        "addChatItemAction": {
            "item": {
                "giftMessageViewModel": {
                    "id": "gift-1",
                    "authorName": {"content": "@K1NGBOB1212 "},
                    "text": {"content": "sent 100 for 2 Jewels"},
                    "rendererContext": {},
                    "image": {},
                    "imageA11yLabel": "Jewels",
                    "authorAvatar": {"avatarViewModel": {}},
                    "giftImage": {"sources": []},
                    "giftImageA11yLabel": "Image of Jewels",
                },
            },
        },
    }

    finalized = _finalize(process_action(action))
    assert finalized is not None
    assert finalized["action_type"] == "add_chat_item"
    assert finalized["message_type"] == "gift_message_view_model"
    assert finalized["message_id"] == "gift-1"
    assert finalized["message"] == "sent 100 for 2 Jewels"
    assert finalized["author"]["name"] == "@K1NGBOB1212 "
    assert logs == []
    assert samples == []


def test_process_action_ignores_interactivity_widget_action(
    monkeypatch,
) -> None:
    logs = []
    samples = []
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.actions_router.debug_log",
        lambda *parts: logs.append(parts),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.actions_router.capture_debug_sample",
        lambda *parts: samples.append(parts),
    )

    action = {
        "addInteractivityWidgetAction": {
            "widgetRenderer": {
                "interactivityWidgetRenderer": {
                    "id": "gift-overlay",
                    "content": {"giftOverlayItemViewModel": {}},
                    "type": "INTERACTIVITY_WIDGET_TYPE_GIFT",
                },
            },
        },
    }

    assert process_action(action) is None
    assert logs == []
    assert samples == []


def test_process_action_remove_actions() -> None:
    # removeChatItemAction -> banUser message type
    action = {
        "removeChatItemAction": {
            "targetItemId": "abc",
            "timestampUsec": "2",
        },
    }
    finalized = _finalize(process_action(action))
    assert finalized is not None
    assert finalized["action_type"] == "remove_chat_item"
    assert finalized["message_type"] == "ban_user"
    assert finalized["target_message_id"] == "abc"
    assert finalized["timestamp"] == 2

    # markChatItemAsDeletedAction -> deletedMessage message type
    action2 = {
        "markChatItemAsDeletedAction": {
            "targetItemId": "def",
            "timestampUsec": "3",
        },
    }
    finalized2 = _finalize(process_action(action2))
    assert finalized2 is not None
    assert finalized2["action_type"] == "mark_chat_item_as_deleted"
    assert finalized2["message_type"] == "deleted_message"
    assert finalized2["target_message_id"] == "def"
    assert finalized2["timestamp"] == 3


def test_process_action_remove_chat_item_by_author_action() -> None:
    action = {
        "removeChatItemByAuthorAction": {
            "externalChannelId": "UCzIZTkKIFheFCx7GwQ6Obrw",
        },
    }

    finalized = _finalize(process_action(action))
    assert finalized is not None
    assert finalized["action_type"] == "remove_chat_item_by_author"
    assert finalized["message_type"] == "ban_user"
    assert finalized["author"]["id"] == "UCzIZTkKIFheFCx7GwQ6Obrw"
    assert finalized["author"]["name"] == ""
    assert finalized["message"] is None


def test_process_action_mark_chat_items_by_author_as_deleted_action() -> None:
    action = {
        "markChatItemsByAuthorAsDeletedAction": {
            "externalChannelId": "UCzIZTkKIFheFCx7GwQ6Obrw",
            "deletedStateMessage": {
                "runs": [{"text": "Message deleted by a moderator."}],
            },
        },
    }

    finalized = _finalize(process_action(action))
    assert finalized is not None
    assert finalized["action_type"] == "mark_chat_items_by_author_as_deleted"
    assert finalized["message_type"] == "ban_user"
    assert finalized["author"]["id"] == "UCzIZTkKIFheFCx7GwQ6Obrw"
    assert finalized["message"] == "Message deleted by a moderator."


def test_process_action_replace_action() -> None:
    action = {
        "replaceChatItemAction": {
            "replacementItem": {
                "liveChatTextMessageRenderer": _renderer_with_timestamp("4"),
            },
        },
    }
    finalized = _finalize(process_action(action))
    assert finalized is not None
    assert finalized["action_type"] == "replace_chat_item"
    assert finalized["message_type"] == "text_message"
    assert finalized["timestamp"] == 4


def test_process_action_tooltip_action() -> None:
    action = {
        "showLiveChatTooltipCommand": {
            "tooltip": {
                "tooltipRenderer": {
                    "detailsText": {"simpleText": "Hello"},
                    "timestampUsec": "5",
                },
            },
        },
    }
    finalized = _finalize(process_action(action))
    assert finalized is not None
    assert finalized["action_type"] == "show_live_chat_tooltip"
    assert finalized["message_type"] == "tooltip"
    assert finalized["timestamp"] == 5


def test_process_action_add_and_remove_banner_actions() -> None:
    action_add = {
        "addBannerToLiveChatCommand": {
            "bannerRenderer": {
                "liveChatBannerRenderer": {
                    "contents": {
                        "liveChatTextMessageRenderer": _renderer_with_timestamp(
                            "6"
                        ),
                    },
                },
            },
        },
    }
    finalized = _finalize(process_action(action_add))
    assert finalized is not None
    assert finalized["action_type"] == "add_banner_to_live_chat"
    assert finalized["message_type"] == "banner"
    assert finalized["timestamp"] == 6

    # Missing bannerRenderer should still return a tuple, but won't have a
    # message type.
    action_add_missing = {"addBannerToLiveChatCommand": {}}
    result = process_action(action_add_missing)
    assert result is not None
    data, original_item, original_message_type, original_action_type = result
    assert original_message_type is None
    assert (
        validate_and_finalize_message(
            data,
            original_item,
            original_message_type,
            original_action_type,
        )
        is None
    )

    action_remove = {
        "removeBannerForLiveChatCommand": {
            "targetActionId": "xyz",
            "timestampUsec": "7",
        },
    }
    finalized2 = _finalize(process_action(action_remove))
    assert finalized2 is not None
    assert finalized2["action_type"] == "remove_banner_for_live_chat"
    assert finalized2["message_type"] == "remove_banner"
    assert finalized2["target_message_id"] == "xyz"
    assert finalized2["timestamp"] == 7


def test_process_action_unknown_action_type_returns_none() -> None:
    assert process_action({"someNewAction": {}}) is None


def test_process_action_unknown_action_type_captures_debug_sample(
    monkeypatch,
) -> None:
    captures = []

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.actions_router.capture_debug_sample",
        lambda label, payload: captures.append((label, payload)),
    )

    assert process_action({"someNewAction": {"foo": "bar"}}) is None
    assert captures == [
        (
            "youtube-unknown-action-someNewAction",
            {
                "action": {"someNewAction": {"foo": "bar"}},
                "parsed_data": {"action_type": "some_new"},
            },
        ),
    ]


def test_process_action_empty_dict_returns_none() -> None:
    """Line 66: empty action has no action type key → return None."""
    assert process_action({}) is None


def test_process_action_only_tracking_params_returns_none() -> None:
    """After popping clickTrackingParams, an empty action returns None."""
    assert process_action({"clickTrackingParams": "abc"}) is None


def test_process_action_known_ignore_action_type_returns_none() -> None:
    """Actions in _KNOWN_IGNORE_ACTION_TYPES are silently dropped."""
    action = {"liveChatReportModerationStateCommand": {"someData": True}}
    assert process_action(action) is None


def test_process_action_creator_goal_ticker_chip_is_known_ignored(
    monkeypatch,
) -> None:
    captures = []
    logs = []

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.actions_router.capture_debug_sample",
        lambda label, payload: captures.append((label, payload)),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.actions_router.debug_log",
        lambda *parts: logs.append(parts),
    )

    action = {
        "showCreatorGoalTickerChipCommand": {
            "creatorGoalTickerChip": {
                "liveChatTickerCreatorGoalViewModel": {
                    "initialTickerText": {"simpleText": "Goal"},
                    "tickerIcon": {"iconType": "TARGET_ADD"},
                    "creatorGoalEntityKey": "goal-key",
                    "shouldShowCountIncrementAnimation": True,
                    "a11yLabel": "See Super Chat goal",
                    "creatorGoalProgressFlowViewModel": {
                        "progressCountA11yLabel": (
                            "Super Chat goal progress: $0 out of $1"
                        ),
                    },
                },
            },
        },
    }

    assert process_action(action) is None
    assert "showCreatorGoalTickerChipCommand" in _KNOWN_ACTION_TYPES
    assert captures == []
    assert logs == []


def test_process_action_show_live_chat_action_panel_poll() -> None:
    action = {
        "showLiveChatActionPanelAction": {
            "panelToShow": {
                "liveChatActionPanelRenderer": {
                    "id": "panel-123",
                    "contents": {
                        "pollRenderer": {
                            "liveChatPollId": "poll-456",
                            "header": {
                                "pollHeaderRenderer": {
                                    "pollQuestion": {
                                        "runs": [{"text": "Best color?"}]
                                    }
                                }
                            },
                            "choices": [
                                {
                                    "text": {"runs": [{"text": "Red"}]},
                                    "voteRatio": 0.6,
                                },
                                {
                                    "text": {"runs": [{"text": "Blue"}]},
                                    "voteRatio": 0.4,
                                },
                            ],
                        }
                    },
                }
            }
        }
    }
    finalized = _finalize(process_action(action))
    assert finalized is not None
    assert finalized["action_type"] == "show_live_chat_action_panel"
    assert finalized["message_type"] == "poll"
    assert finalized["poll_id"] == "poll-456"
    assert finalized["poll_question"] == "Best color?"
    assert finalized["poll_choices"] == [
        {"text": "Red", "vote_ratio": 0.6, "selected": False},
        {"text": "Blue", "vote_ratio": 0.4, "selected": False},
    ]


def test_process_action_update_live_chat_poll() -> None:
    action = {
        "updateLiveChatPollAction": {
            "pollToUpdate": {
                "pollRenderer": {
                    "liveChatPollId": "poll-456",
                    "choices": [
                        {
                            "text": {"runs": [{"text": "Red"}]},
                            "voteRatio": 0.7,
                            "selected": True,
                        },
                        {
                            "text": {"runs": [{"text": "Blue"}]},
                            "voteRatio": 0.3,
                        },
                    ],
                }
            }
        }
    }
    finalized = _finalize(process_action(action))
    assert finalized is not None
    assert finalized["action_type"] == "update_live_chat_poll"
    assert finalized["message_type"] == "poll"
    assert finalized["poll_id"] == "poll-456"
    assert finalized["poll_choices"][0]["vote_ratio"] == 0.7
    assert finalized["poll_choices"][0]["selected"] is True


def test_process_action_close_live_chat_action_panel() -> None:
    action = {
        "closeLiveChatActionPanelAction": {
            "targetPanelId": "panel-123",
        }
    }
    finalized = _finalize(process_action(action))
    assert finalized is not None
    assert finalized["action_type"] == "close_live_chat_action_panel"
    assert finalized["message_type"] == "poll_closed_event"
    assert finalized["poll_id"] == "panel-123"


def test_process_action_mode_change_slow_mode() -> None:
    action = {
        "addChatItemAction": {
            "item": {
                "liveChatModeChangeMessageRenderer": {
                    "id": "mode-1",
                    "icon": {"iconType": "SLOW_MODE"},
                    "timestampUsec": "9",
                },
            }
        }
    }
    finalized = _finalize(process_action(action))
    assert finalized is not None
    assert finalized["message_type"] == "slow_mode_message"


def test_process_action_mode_change_members_only() -> None:
    action = {
        "addChatItemAction": {
            "item": {
                "liveChatModeChangeMessageRenderer": {
                    "id": "mode-2",
                    "icon": {"iconType": "MEMBERS_ONLY"},
                    "timestampUsec": "10",
                },
            }
        }
    }
    finalized = _finalize(process_action(action))
    assert finalized is not None
    assert finalized["message_type"] == "members_only_mode_message"


def test_process_action_mode_change_unknown_icon_falls_back() -> None:
    action = {
        "addChatItemAction": {
            "item": {
                "liveChatModeChangeMessageRenderer": {
                    "id": "mode-3",
                    "icon": {"iconType": "UNKNOWN_NEW_MODE"},
                    "timestampUsec": "11",
                },
            }
        }
    }
    finalized = _finalize(process_action(action))
    assert finalized is not None
    assert finalized["message_type"] == "mode_change_message"


def test_process_action_paid_sticker_with_pdg_logging_directives(
    monkeypatch,
) -> None:
    captures = []
    logs = []

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.actions_handlers_validation.capture_debug_sample",
        lambda label, payload: captures.append((label, payload)),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.actions_handlers_validation.debug_log",
        lambda *parts: logs.append(parts),
    )

    action = {
        "addChatItemAction": {
            "item": {
                "liveChatPaidStickerRenderer": {
                    "id": "sticker-1",
                    "authorExternalChannelId": "UC123",
                    "authorName": {"simpleText": "@viewer"},
                    "purchaseAmountText": {"simpleText": "$1.99"},
                    "sticker": {
                        "thumbnails": [
                            {
                                "url": "https://img.example/sticker=s64",
                                "width": 64,
                                "height": 64,
                            },
                        ],
                    },
                    "timestampUsec": "12",
                    "pdgPurchasedNoveltyLoggingDirectives": {
                        "trackingParams": "opaque",
                    },
                },
            },
        },
    }

    finalized = _finalize(process_action(action))
    assert finalized is not None
    assert finalized["action_type"] == "add_chat_item"
    assert finalized["message_type"] == "paid_sticker"
    assert finalized["message_id"] == "sticker-1"
    assert finalized["author"] == {"id": "UC123", "name": "@viewer"}
    assert finalized["money"] == {
        "amount": 1.99,
        "currency": "USD",
        "currency_symbol": "$",
        "text": "$1.99",
    }
    assert finalized["sticker_images"][0]["id"] == "source"
    assert finalized["sticker_images"][1]["id"] == "64x64"
    assert captures == []
    assert logs == []


def test_validate_and_finalize_message_empty_data_logs_and_continues() -> None:
    """debug_log fires on an empty data dict, but result is returned."""
    result = validate_and_finalize_message(
        {},
        {"liveChatTextMessageRenderer": {}},
        "liveChatTextMessageRenderer",
        "addChatItemAction",
    )
    # data is empty but message_type is set; result is returned (not None)
    assert result is not None
    assert result.get("message_type") == "text_message"


def test_validate_and_finalize_message_without_message_type_returns_none(
    monkeypatch,
) -> None:
    logs = []
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.actions_handlers_validation.debug_log",
        lambda *parts: logs.append(parts),
    )

    result = validate_and_finalize_message(
        {"timestamp": 1},
        {"someRenderer": {}},
        None,
        "addChatItemAction",
    )

    assert result is None
    assert logs == [("No message type", "Action type: addChatItemAction")]


def test_validate_and_finalize_message_unknown_keys_logs_and_continues() -> (
    None
):
    """Lines 203-207: debug_log fires when item has keys not in _KNOWN_KEYS."""
    item = {"liveChatTextMessageRenderer": {"unknownField2026XYZ": "value"}}
    result = validate_and_finalize_message(
        {"timestamp": 1},
        item,
        "liveChatTextMessageRenderer",
        "addChatItemAction",
    )
    assert result is not None
    assert result.get("message_type") == "text_message"


def test_validate_and_finalize_message_known_ignore_message_type_returns_none() -> (  # noqa: E501
    None
):
    """Line 218: messages in _KNOWN_IGNORE_MESSAGE_TYPES are dropped."""
    result = validate_and_finalize_message(
        {"timestamp": 1},
        {"liveChatPlaceholderItemRenderer": {}},
        "liveChatPlaceholderItemRenderer",
        "addChatItemAction",
    )
    assert result is None


def test_validate_and_finalize_message_unknown_message_type_does_not_throw() -> (  # noqa: E501
    None
):
    action = {
        "replayChatItemAction": {
            "videoOffsetTimeMsec": "1",
            "actions": [
                {
                    "addChatItemAction": {
                        "item": {
                            "liveChatMadeUpRenderer": _renderer_with_timestamp(
                                "8"
                            ),
                        },
                    },
                },
            ],
        },
    }
    finalized = _finalize(process_action(action))
    assert finalized is not None
    assert finalized["action_type"] == "add_chat_item"
    # Normalized from "liveChatMadeUpRenderer" -> "made_up"
    assert finalized["message_type"] == "made_up"


def test_validate_and_finalize_message_missing_keys_captures_debug_sample(
    monkeypatch,
) -> None:
    captures = []
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.actions_handlers_validation.capture_debug_sample",
        lambda label, payload: captures.append((label, payload)),
    )

    result = validate_and_finalize_message(
        {"timestamp": 1},
        {"liveChatMadeUpRenderer": {"unknownField2026XYZ": "value"}},
        "liveChatMadeUpRenderer",
        "addChatItemAction",
    )

    assert result is not None
    assert captures[0] == (
        "youtube-missing-keys-liveChatMadeUpRenderer",
        {
            "original_item": {
                "liveChatMadeUpRenderer": {"unknownField2026XYZ": "value"},
            },
            "original_action_type": "addChatItemAction",
            "original_message_type": "liveChatMadeUpRenderer",
            "missing_keys": ["unknownField2026XYZ"],
        },
    )


def test_validate_and_finalize_message_unknown_message_type_captures_debug_sample(  # noqa: E501
    monkeypatch,
) -> None:
    captures = []
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.actions_handlers_validation.capture_debug_sample",
        lambda label, payload: captures.append((label, payload)),
    )

    result = validate_and_finalize_message(
        {"timestamp": 1},
        {"liveChatMadeUpRenderer": {}},
        "liveChatMadeUpRenderer",
        "addChatItemAction",
    )

    assert result is not None
    assert captures[-1] == (
        "youtube-unknown-message-type-liveChatMadeUpRenderer",
        {
            "data": {"timestamp": 1, "message_type": "made_up"},
            "original_item": {"liveChatMadeUpRenderer": {}},
            "original_action_type": "addChatItemAction",
            "original_message_type": "liveChatMadeUpRenderer",
        },
    )
