# SPDX-License-Identifier: MIT

"""Message-level constants for YouTube parsing and remapping."""

from __future__ import annotations

from functools import cache
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from chat_downloader.sites.remap import (
    Remapper as r,  # noqa: N813 — compact table-construction alias; used as r("key", ...) throughout remapping tables
)

if TYPE_CHECKING:
    from collections.abc import Mapping

# Message groups and flat type list
_MESSAGE_GROUPS = {
    "messages": ["text_message"],  # normal message
    "superchat": [
        # superchat messages which appear in chat
        "membership_item",
        "paid_message",
        "paid_sticker",
        # Gifts
        "gift_message_view_model",
        "sponsorships_gift_purchase_announcement",
        "sponsorships_gift_redemption_announcement",
        "sponsorships_header",
    ],
    "tickers": [
        # superchat messages which appear ticker (at the top)
        "ticker_paid_sticker_item",
        "ticker_paid_message_item",
        "ticker_sponsor_item",
    ],
    "banners": ["banner", "banner_header", "banner_chat_summary"],
    "donations": ["donation_announcement"],
    "engagement": [
        # message saying live chat replay is on
        "viewer_engagement_message",
        # synthetic end-of-stream marker
        "chat_ended",
    ],
    "purchases": ["purchased_product_message"],  # product purchased/promoted
    "moderation": ["auto_mod_message", "restricted_participation"],
    "mode_changes": [
        "mode_change_message",  # generic fallback for unknown icon types
        "slow_mode_message",
        "members_only_mode_message",
        "subscribers_only_mode_message",
        "emote_only_mode_message",
    ],
    "polls": ["poll", "poll_closed_event"],
    "deleted": ["deleted_message"],
    "bans": ["ban_user"],
    "placeholder": ["placeholder_item"],  # placeholder
}

_MESSAGE_TYPES = ["all"]
for _group_types in _MESSAGE_GROUPS.values():
    _MESSAGE_TYPES += _group_types

# Currency symbols mapping
_CURRENCY_SYMBOLS = {
    "$": "USD",
    "A$": "AUD",
    "CA$": "CAD",
    "HK$": "HKD",
    "MX$": "MXN",
    "NT$": "TWD",
    "NZ$": "NZD",
    "R$": "BRL",
    "£": "GBP",
    "€": "EUR",
    "₹": "INR",
    "₪": "ILS",
    "₱": "PHP",
    "₩": "KRW",
    "￦": "KRW",
    "¥": "JPY",
    "￥": "JPY",
    "؋": "AFN",
    "฿": "THB",
    "₵": "GHS",
    "₡": "CRC",
    "₫": "VND",
    "֏": "AMD",
    "₲": "PYG",
    "₴": "UAH",
    "₭": "LAK",
    "₾": "GEL",
    "₺": "TRY",
    "₼": "AZN",
    "₦": "NGN",
    "﷼": "IRR",
    "៛": "KHR",
    "₽": "RUB",
    "⃀": "KGS",
    "৳": "BDT",
    "₸": "KZT",
    "₮": "MNT",
}

# All other currency symbols use the ISO 4217 format:
# https://en.wikipedia.org/wiki/ISO_4217
# e.g. 'CHF', 'COP', 'HUF', 'PLN', 'RUB', 'SEK', 'PEN', 'ARS', 'CLP',
# 'NOK', 'BAM', 'SGD'

# Remapping key metadata
_COLOUR_KEYS = [
    # paid_message
    "authorNameTextColor",
    "timestampColor",
    "bodyBackgroundColor",
    "headerTextColor",
    "headerBackgroundColor",
    "bodyTextColor",
    "textInputBackgroundColor",
    # paid_sticker
    "backgroundColor",
    "moneyChipTextColor",
    "moneyChipBackgroundColor",
    # ticker_paid_message_item
    "startBackgroundColor",
    "amountTextColor",
    "endBackgroundColor",
    # ticker_sponsor_item
    "detailTextColor",
]

_STICKER_KEYS = [
    # to actually ignore
    "stickerDisplayWidth",
    "stickerDisplayHeight",  # ignore
    # parsed elsewhere
    "sticker",
]

_KEYS_TO_IGNORE = [
    # to actually ignore
    "contextMenuAccessibility",
    "contextMenuEndpoint",
    "trackingParams",
    "accessibility",
    "dwellTimeMs",
    "empty",  # signals liveChatMembershipItemRenderer has no message body
    "contextMenuButton",
    # parsed elsewhere
    "showItemEndpoint",
    "durationSec",
    # banner parsed elsewhere
    "header",
    "contents",
    "actionId",
    # tooltipRenderer
    "dismissStrategy",
    "suggestedPosition",
    "promoConfig",
    # redundant field for ticker renderer
    "authorUsername",
    # new YouTube UI fields (2024+)
    "replyButton",
    "likeButton",
    "dislikeButton",
    "isV2Style",
    "beforeContentButtons",
    "animationOrigin",
    "openEngagementPanelCommand",
    # giftMessageViewModel presentation metadata
    "rendererContext",
    "image",
    "imageA11yLabel",
    "authorAvatar",
    "giftImage",
    "giftImageA11yLabel",
    # ticker UI state metadata (not chat content)
    "dynamicStateData",
    # paid sticker purchase/logging metadata
    "pdgPurchasedNoveltyLoggingDirectives",
    # chat-summary banner feedback/UI metadata
    "likeFeedbackButton",
    "dislikeFeedbackButton",
    "overflowMenuButton",
    "loggingDirectives",
    "collapsedStateEntityKey",
    # product item UI surfaces
    "informationButton",
    "informationDialog",
]


@cache
def build_remapping() -> Mapping[str, Any]:
    """Build the full _REMAPPING dictionary with parsing functions.

    This function is called to create the complete remapping configuration that
    includes references to the focused parsing modules.

    :return: Complete remapping dictionary
    :rtype: dict
    """
    from chat_downloader.utils.conversion_utils import int_or_none

    from .parsing.message_content_badges import _parse_badges, _parse_currency
    from .parsing.message_content_text_parser import (
        _get_simple_text,
        _parse_action_button,
        _parse_runs,
        _parse_text,
        _parse_thumbnails,
    )
    from .parsing.message_items_content_parser import _parse_item
    from .parsing.message_links import _parse_navigation_endpoint

    return MappingProxyType(
        {
            "id": "message_id",
            "authorExternalChannelId": "author_id",
            "authorName": r("author_name", _get_simple_text),
            "purchaseAmountText": r("money", _parse_currency),
            "message": r(None, _parse_runs, to_unpack=True),
            "timestampText": r("time_text", _get_simple_text),
            "timestampUsec": r("timestamp", int_or_none),
            "authorPhoto": r("author_images", _parse_thumbnails),
            "tooltip": "tooltip",
            "icon": r("icon", lambda x: x.get("iconType")),
            "authorBadges": r("author_badges", _parse_badges),
            # stickers
            "sticker": r("sticker_images", _parse_thumbnails),
            # ticker_paid_message_item
            "fullDurationSec": r("ticker_duration", int_or_none),
            "amount": r("money", _parse_currency),
            # ticker_sponsor_item
            "detailText": r(None, _parse_runs, to_unpack=True),
            "detailIcon": r("detail_icon", lambda x: x.get("iconType")),
            "customThumbnail": r("badge_icons", _parse_thumbnails),
            # membership_item
            "headerPrimaryText": r("header_primary_text", _parse_text),
            "headerSubtext": r("header_secondary_text", _parse_text),
            "sponsorPhoto": r("sponsor_icons", _parse_thumbnails),
            # ticker_paid_sticker_item
            "tickerThumbnails": r("ticker_icons", _parse_thumbnails),
            # deleted messages
            "deletedStateMessage": r(None, _parse_runs, to_unpack=True),
            "targetItemId": "target_message_id",
            "externalChannelId": "author_id",
            # action buttons
            "actionButton": r("action", _parse_action_button),
            # addBannerToLiveChatCommand
            "liveChatSummaryId": "summary_id",
            "chatSummary": r(None, _parse_runs, to_unpack=True),
            "text": r(None, _parse_runs, to_unpack=True),
            "viewerIsCreator": "viewer_is_creator",
            "targetId": "target_message_id",
            "isStackable": "is_stackable",
            "backgroundType": "background_type",
            # removeBannerForLiveChatCommand
            "targetActionId": "target_message_id",
            # donation_announcement
            "subtext": r(None, _parse_runs, to_unpack=True),
            # tooltip
            "detailsText": r(None, _parse_runs, to_unpack=True),
            # gifts
            "primaryText": r("message", _parse_text),
            "bannerType": "banner_type",
            "bannerProperties": "banner_properties",
            "headerOverlayImage": r("header_overlay_image", _parse_thumbnails),
            # hearted message
            "creatorHeartButton": "creator_heart_button",
            # paid message metadata (2026+)
            "leaderboardBadge": r("leaderboard_badge", _parse_item),
            # product item
            "title": "product_title",
            "accessibilityTitle": "product_accessibility_title",
            "thumbnail": r("product_images", _parse_thumbnails),
            "price": "price",
            "vendorName": "vendor_name",
            "fromVendorText": "from_vendor_text",
            "onClickCommand": r("url", _parse_navigation_endpoint),
            "creatorMessage": "creator_message",
            "creatorName": "creator_name",
            "creatorCustomMessage": r("message", _parse_text),
            "isVerified": "is_verified",
            # restricted participation / automod
            "autoModeratedItem": r("auto_moderated_item", _parse_item),
            "headerText": r("header_text", _parse_text),
            "moderationButtons": "moderation_buttons",
            # other
            "lowerBumper": "lower_bumper",
        }
    )


@cache
def build_video_remapping() -> Mapping[str, Any]:
    """Build the full _VIDEO_REMAPPING dictionary with parsing functions.

    This function is called to create the complete video remapping
    configuration that includes references to parsing functions from the
    focused parsing modules.

    :return: Complete video remapping dictionary
    :rtype: dict
    """
    from .parsing.message_content_text_parser import _parse_runs, _parse_text

    return MappingProxyType(
        {
            "videoId": "video_id",
            "title": r("title", lambda x: _parse_runs(x)["message"]),
            "videoType": "video_type",
            "viewCountText": r("view_count", _parse_text),
            "shortViewCountText": r("short_view_count", _parse_text),
        }
    )


@cache
def known_keys() -> frozenset[str]:
    """All payload keys the parser handles or deliberately ignores.

    Derived from ``build_remapping()`` so there is a single source of truth;
    used to detect newly-introduced (unknown) keys for drift logging.
    """
    return frozenset(build_remapping()) | frozenset(
        _COLOUR_KEYS + _STICKER_KEYS + _KEYS_TO_IGNORE,
    )
