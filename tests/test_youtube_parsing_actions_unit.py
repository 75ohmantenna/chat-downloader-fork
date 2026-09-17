# SPDX-License-Identifier: MIT

from __future__ import annotations

from collections.abc import Mapping

import pytest

import chat_downloader.debugging as dbg
from chat_downloader.sites.youtube.constants_message import build_video_remapping
from chat_downloader.sites.youtube.parsing.actions_handlers_validation import (
    validate_and_finalize_message,
)
from chat_downloader.sites.youtube.parsing.actions_router import process_action
from tests.youtube_third_helpers import item_action, wrap


def _jewels_action(attribution):
    return wrap(
        "updateOrAddInteractivityWidgetAction.widgetRenderer."
        "interactivityWidgetRenderer.content.giftAttributionItemViewModel",
        attribution,
    )


def _finalize(action):
    result = process_action(action)
    assert result is not None
    return validate_and_finalize_message(
        result.parsed_data,
        result.original_item,
        result.message_type,
        result.action_type,
    )


def _assert_fields(result, expected):
    assert result is not None
    for path, value in expected.items():
        actual = result
        for key in path.split("."):
            actual = actual[int(key)] if isinstance(actual, list) else actual[key]
        assert actual == value, path


@pytest.fixture
def diagnostics(monkeypatch):
    def capture(module):
        logs, samples = [], []
        prefix = f"chat_downloader.sites.youtube.parsing.{module}"
        monkeypatch.setattr(f"{prefix}.debug_log", lambda *parts: logs.append(parts))
        monkeypatch.setattr(
            f"{prefix}.capture_debug_sample",
            lambda *parts, **kwargs: samples.append((*parts, kwargs)),
        )
        return logs, samples

    return capture


def setup_module() -> None:
    dbg.set_testing_mode(dbg.TestingModes.NONE)


def test_build_video_remapping_returns_mapping_with_expected_keys() -> None:
    mapping = build_video_remapping()
    assert isinstance(mapping, Mapping)
    assert {"videoId", "title"} <= mapping.keys()


@pytest.mark.parametrize("replay", [None, {}, {"videoOffsetTimeMsec": "2345"}])
def test_process_action_add_chat_item_with_optional_replay(replay) -> None:
    action = item_action("liveChatTextMessageRenderer", {"timestampUsec": "1234567890"})
    if replay is not None:
        action = {"replayChatItemAction": {**replay, "actions": [action]}}
    finalized = _finalize(action)
    _assert_fields(
        finalized,
        {
            "action_type": "add_chat_item",
            "message_type": "text_message",
            "timestamp": 1234567890,
        },
    )
    if replay:
        assert finalized["time_in_seconds"] == pytest.approx(2.345)
    else:
        assert "time_in_seconds" not in finalized


@pytest.mark.parametrize(
    "action",
    [
        wrap("updateOrAddInteractivityWidgetAction.widgetRenderer", {}),
        _jewels_action({"unexpected": True}),
        _jewels_action({"id": "gift-1", "authorName": {"content": "@sender"}}),
        _jewels_action({"id": "gift-1", "detailText": {"content": "Sent a gift"}}),
        {"addBannerToLiveChatCommand": {}},
    ],
)
def test_incomplete_widget_or_banner_is_skipped(action):
    assert _finalize(action) is None


def test_process_action_minimal_jewels_widget_omits_optional_fields():
    finalized = _finalize(
        _jewels_action(
            {
                "id": "gift-1",
                "authorName": {"content": "@sender"},
                "detailText": {"content": "Sent a gift"},
            }
        )
    )
    _assert_fields(
        finalized,
        {
            "message_id": "gift-1",
            "message": "Sent a gift",
            "author.name": "@sender",
        },
    )
    assert "combo_count" not in finalized


@pytest.mark.parametrize(
    ("renderer", "content", "expected"),
    [
        (
            "giftMessageViewModel",
            {
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
            {
                "action_type": "add_chat_item",
                "message_type": "gift_message_view_model",
                "message_id": "gift-1",
                "message": "sent 100 for 2 Jewels",
                "author.name": "@K1NGBOB1212 ",
            },
        ),
        (
            "liveChatProductItemRenderer",
            {
                "title": "Channel hoodie",
                "accessibilityTitle": "Channel hoodie product",
                "thumbnail": {
                    "thumbnails": [
                        {
                            "url": "https://example.invalid/hoodie=s88",
                            "width": 88,
                            "height": 88,
                        }
                    ]
                },
                "price": "$25.00",
                "vendorName": "Creator shop",
                "fromVendorText": "from Creator shop",
                "onClickCommand": wrap(
                    "commandMetadata.webCommandMetadata.url",
                    "https://example.invalid/product",
                ),
                "creatorMessage": "Pinned product",
                "creatorName": "Creator",
                "creatorCustomMessage": {"content": "New merch"},
                "authorPhoto": {"thumbnails": []},
                "informationButton": {},
                "informationDialog": {},
                "isVerified": True,
                "timestampUsec": "11",
            },
            {
                "message_type": "purchased_product_message",
                "product_title": "Channel hoodie",
                "product_accessibility_title": "Channel hoodie product",
                "price": "$25.00",
                "vendor_name": "Creator shop",
                "url": "https://example.invalid/product",
                "message": "New merch",
                "product_images.0.url": "https://example.invalid/hoodie",
            },
        ),
        (
            "liveChatRestrictedParticipationRenderer",
            {
                "message": {"runs": [{"text": "Only subscribers can send messages"}]},
                "icon": {"iconType": "SUBSCRIBERS_ONLY"},
                "timestampUsec": "12",
            },
            {
                "message_type": "restricted_participation",
                "icon": "SUBSCRIBERS_ONLY",
                "message": "Only subscribers can send messages",
            },
        ),
        (
            "liveChatAutoModMessageRenderer",
            {
                "id": "auto-1",
                "headerText": {"content": "Held for review"},
                "timestampUsec": "13",
                "contextMenuEndpoint": {},
                "moderationButtons": [{"buttonRenderer": {}}],
                "autoModeratedItem": {
                    "liveChatTextMessageRenderer": {
                        "id": "held-1",
                        "authorName": {"simpleText": "@HeldUser"},
                        "message": {"runs": [{"text": "blocked text"}]},
                        "timestampUsec": "13",
                    }
                },
            },
            {
                "message_type": "auto_mod_message",
                "message_id": "auto-1",
                "header_text": "Held for review",
                "auto_moderated_item.message_id": "held-1",
                "auto_moderated_item.message": "blocked text",
                "auto_moderated_item.author.name": "@HeldUser",
            },
        ),
        (
            "liveChatPaidStickerRenderer",
            {
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
                        }
                    ]
                },
                "timestampUsec": "12",
                "pdgPurchasedNoveltyLoggingDirectives": {"trackingParams": "opaque"},
            },
            {
                "action_type": "add_chat_item",
                "message_type": "paid_sticker",
                "message_id": "sticker-1",
                "author": {"id": "UC123", "name": "@viewer"},
                "money": {
                    "amount": 1.99,
                    "currency": "USD",
                    "currency_symbol": "$",
                    "text": "$1.99",
                },
                "sticker_images.0.id": "source",
                "sticker_images.1.id": "64x64",
            },
        ),
    ],
)
def test_recognized_item_renderers_without_diagnostics(
    diagnostics, renderer, content, expected
):
    logs, samples = diagnostics("actions_handlers_validation")
    _assert_fields(_finalize(item_action(renderer, content)), expected)
    assert logs == samples == []


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (
            wrap("removeChatItemAction", {"targetItemId": "abc", "timestampUsec": "2"}),
            {
                "action_type": "remove_chat_item",
                "message_type": "ban_user",
                "target_message_id": "abc",
                "timestamp": 2,
            },
        ),
        (
            wrap(
                "markChatItemAsDeletedAction",
                {
                    "targetItemId": "def",
                    "timestampUsec": "3",
                },
            ),
            {
                "action_type": "mark_chat_item_as_deleted",
                "message_type": "deleted_message",
                "target_message_id": "def",
                "timestamp": 3,
            },
        ),
        (
            wrap(
                "removeChatItemByAuthorAction.externalChannelId",
                "UCzIZTkKIFheFCx7GwQ6Obrw",
            ),
            {
                "action_type": "remove_chat_item_by_author",
                "message_type": "ban_user",
                "author.id": "UCzIZTkKIFheFCx7GwQ6Obrw",
                "author.name": "",
                "message": None,
            },
        ),
        (
            wrap(
                "markChatItemsByAuthorAsDeletedAction",
                {
                    "externalChannelId": "UCzIZTkKIFheFCx7GwQ6Obrw",
                    "deletedStateMessage": {
                        "runs": [{"text": "Message deleted by a moderator."}]
                    },
                },
            ),
            {
                "action_type": "mark_chat_items_by_author_as_deleted",
                "message_type": "ban_user",
                "author.id": "UCzIZTkKIFheFCx7GwQ6Obrw",
                "message": "Message deleted by a moderator.",
            },
        ),
        (
            wrap(
                "replaceChatItemAction.replacementItem.liveChatTextMessageRenderer",
                {
                    "timestampUsec": "4",
                },
            ),
            {
                "action_type": "replace_chat_item",
                "message_type": "text_message",
                "timestamp": 4,
            },
        ),
        (
            wrap(
                "showLiveChatTooltipCommand.tooltip.tooltipRenderer",
                {
                    "detailsText": {"simpleText": "Hello"},
                    "timestampUsec": "5",
                },
            ),
            {
                "action_type": "show_live_chat_tooltip",
                "message_type": "tooltip",
                "timestamp": 5,
            },
        ),
        (
            wrap(
                "addBannerToLiveChatCommand.bannerRenderer.liveChatBannerRenderer."
                "contents.liveChatTextMessageRenderer",
                {"timestampUsec": "6"},
            ),
            {
                "action_type": "add_banner_to_live_chat",
                "message_type": "banner",
                "timestamp": 6,
            },
        ),
        (
            wrap(
                "removeBannerForLiveChatCommand",
                {
                    "targetActionId": "xyz",
                    "timestampUsec": "7",
                },
            ),
            {
                "action_type": "remove_banner_for_live_chat",
                "message_type": "remove_banner",
                "target_message_id": "xyz",
                "timestamp": 7,
            },
        ),
        (
            wrap("closeLiveChatActionPanelAction.targetPanelId", "panel-123"),
            {
                "action_type": "close_live_chat_action_panel",
                "message_type": "poll_closed_event",
                "poll_id": "panel-123",
            },
        ),
    ],
)
def test_action_finalization(action, expected):
    _assert_fields(_finalize(action), expected)


@pytest.mark.parametrize(
    ("action", "silent"),
    [
        ({}, False),
        ({"clickTrackingParams": "abc"}, False),
        ({"liveChatReportModerationStateCommand": {"someData": True}}, False),
        (
            wrap(
                "addInteractivityWidgetAction.widgetRenderer.interactivityWidgetRenderer",
                {
                    "id": "gift-overlay",
                    "content": {"giftOverlayItemViewModel": {}},
                    "type": "INTERACTIVITY_WIDGET_TYPE_GIFT",
                },
            ),
            True,
        ),
        (
            wrap(
                "showCreatorGoalTickerChipCommand.creatorGoalTickerChip."
                "liveChatTickerCreatorGoalViewModel",
                {
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
            ),
            True,
        ),
    ],
)
def test_known_ignored_actions(diagnostics, action, silent):
    logs, samples = diagnostics("actions_router")
    assert process_action(action) is None
    if silent:
        assert logs == samples == []


@pytest.mark.parametrize("content", [{}, {"foo": "bar"}])
def test_unknown_action_captures_debug_sample(diagnostics, content):
    _, captures = diagnostics("actions_router")
    action = {"someNewAction": content}
    assert process_action(action) is None
    assert captures == [
        (
            "youtube-unknown-action-someNewAction",
            {"action": action, "parsed_data": {"action_type": "some_new"}},
            {"sample_limit": 10},
        )
    ]


@pytest.mark.parametrize(
    ("action", "expected", "missing"),
    [
        (
            wrap(
                "showLiveChatActionPanelAction.panelToShow.liveChatActionPanelRenderer",
                {
                    "id": "panel-123",
                    "contents": {
                        "pollRenderer": {
                            "liveChatPollId": "poll-456",
                            "header": wrap(
                                "pollHeaderRenderer.pollQuestion.runs",
                                [{"text": "Best color?"}],
                            ),
                            "choices": [
                                {"text": {"runs": [{"text": "Red"}]}, "voteRatio": 0.6},
                                {
                                    "text": {"runs": [{"text": "Blue"}]},
                                    "voteRatio": 0.4,
                                },
                            ],
                        }
                    },
                },
            ),
            {
                "action_type": "show_live_chat_action_panel",
                "poll_id": "poll-456",
                "poll_question": "Best color?",
                "poll_choices": [
                    {"text": "Red", "vote_ratio": 0.6, "selected": False},
                    {"text": "Blue", "vote_ratio": 0.4, "selected": False},
                ],
            },
            (),
        ),
        (
            {"showLiveChatActionPanelAction": {}},
            {
                "action_type": "show_live_chat_action_panel",
                "poll_choices": [],
            },
            ("poll_id",),
        ),
        (
            wrap(
                "showLiveChatActionPanelAction.panelToShow.liveChatActionPanelRenderer",
                {
                    "id": "panel-123",
                    "contents": {"pollRenderer": {"choices": []}},
                },
            ),
            {"poll_id": "panel-123", "poll_choices": []},
            (),
        ),
        (
            wrap(
                "updateLiveChatPollAction.pollToUpdate.pollRenderer",
                {
                    "liveChatPollId": "poll-456",
                    "choices": [
                        {
                            "text": {"runs": [{"text": "Red"}]},
                            "voteRatio": 0.7,
                            "selected": True,
                        },
                        {"text": {"runs": [{"text": "Blue"}]}, "voteRatio": 0.3},
                    ],
                },
            ),
            {
                "action_type": "update_live_chat_poll",
                "poll_id": "poll-456",
                "poll_choices.0.vote_ratio": 0.7,
                "poll_choices.0.selected": True,
            },
            (),
        ),
    ],
)
def test_poll_actions(action, expected, missing):
    finalized = _finalize(action)
    _assert_fields(finalized, {"message_type": "poll", **expected})
    assert all(key not in finalized for key in missing)


@pytest.mark.parametrize(
    ("icon", "message_type"),
    [
        ("SLOW_MODE", "slow_mode_message"),
        ("MEMBERS_ONLY", "members_only_mode_message"),
        ("UNKNOWN_NEW_MODE", "mode_change_message"),
    ],
)
def test_process_action_mode_change(icon, message_type):
    finalized = _finalize(
        item_action(
            "liveChatModeChangeMessageRenderer",
            {
                "id": "mode-1",
                "icon": {"iconType": icon},
                "timestampUsec": "9",
            },
        )
    )
    assert finalized["message_type"] == message_type


@pytest.mark.parametrize(
    ("data", "renderer"),
    [
        ({}, {}),
        ({"timestamp": 1}, {"unknownField2026XYZ": "value"}),
    ],
)
def test_validate_and_finalize_message_continues_with_text_type(data, renderer):
    result = validate_and_finalize_message(
        data,
        {"liveChatTextMessageRenderer": renderer},
        "liveChatTextMessageRenderer",
        "addChatItemAction",
    )
    assert result["message_type"] == "text_message"


@pytest.mark.parametrize(
    ("message_type", "item", "expected_logs"),
    [
        (
            None,
            {"someRenderer": {}},
            [("No message type", "Action type: addChatItemAction")],
        ),
        (
            "liveChatPlaceholderItemRenderer",
            {"liveChatPlaceholderItemRenderer": {}},
            None,
        ),
    ],
)
def test_finalize_missing_or_ignored_message_type(
    diagnostics, message_type, item, expected_logs
):
    logs, _ = diagnostics("actions_handlers_validation")
    assert (
        validate_and_finalize_message(
            {"timestamp": 1}, item, message_type, "addChatItemAction"
        )
        is None
    )
    if expected_logs is not None:
        assert logs == expected_logs


def test_unknown_replay_message_type_does_not_throw():
    finalized = _finalize(
        {
            "replayChatItemAction": {
                "videoOffsetTimeMsec": "1",
                "actions": [
                    item_action("liveChatMadeUpRenderer", {"timestampUsec": "8"})
                ],
            }
        }
    )
    _assert_fields(
        finalized, {"action_type": "add_chat_item", "message_type": "made_up"}
    )


@pytest.mark.parametrize(
    ("renderer", "index", "label", "details"),
    [
        (
            {"unknownField2026XYZ": "value"},
            0,
            "missing-keys",
            {
                "missing_keys": ["unknownField2026XYZ"],
            },
        ),
        (
            {},
            -1,
            "unknown-message-type",
            {
                "data": {"timestamp": 1, "message_type": "made_up"},
            },
        ),
    ],
)
def test_finalize_unknown_renderer_debug_samples(
    diagnostics, renderer, index, label, details
):
    _, captures = diagnostics("actions_handlers_validation")
    item = {"liveChatMadeUpRenderer": renderer}
    result = validate_and_finalize_message(
        {"timestamp": 1}, item, "liveChatMadeUpRenderer", "addChatItemAction"
    )
    assert result is not None
    assert captures[index] == (
        f"youtube-{label}-liveChatMadeUpRenderer",
        {
            "original_item": item,
            "original_action_type": "addChatItemAction",
            "original_message_type": "liveChatMadeUpRenderer",
            **details,
        },
        {"sample_limit": 10},
    )
