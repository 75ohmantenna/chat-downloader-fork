# SPDX-License-Identifier: MIT

"""Core action mapping constants."""

# Payload wrapper-path keys used when extracting items from action dicts.
# Centralised so a YouTube API rename is a one-line change, and a typo becomes
# a NameError at import rather than a silently-empty parse result.
_PATH_ITEM = "item"
_PATH_REPLACEMENT_ITEM = "replacementItem"
_PATH_TOOLTIP = "tooltip"
_PATH_BANNER_RENDERER = "bannerRenderer"
_RENDERER_BANNER_CHAT_SUMMARY = "liveChatBannerChatSummaryRenderer"

# Action type mappings
_KNOWN_ADD_TICKER_TYPES = {
    "addLiveChatTickerItemAction": [
        "liveChatTickerSponsorItemRenderer",
        "liveChatTickerPaidStickerItemRenderer",
        "liveChatTickerPaidMessageItemRenderer",
    ],
}

_KNOWN_ADD_ACTION_TYPES = {
    "addChatItemAction": [
        # message saying Live Chat replay is on
        "liveChatViewerEngagementMessageRenderer",
        "liveChatMembershipItemRenderer",
        "liveChatTextMessageRenderer",
        "liveChatPaidMessageRenderer",
        "liveChatPlaceholderItemRenderer",  # placeholder
        "liveChatDonationAnnouncementRenderer",
        "liveChatPaidStickerRenderer",
        "liveChatModeChangeMessageRenderer",  # e.g. slow mode enabled
        "liveChatAutoModMessageRenderer",
        "liveChatProductItemRenderer",
        "liveChatRestrictedParticipationRenderer",
        # Gifting
        "giftMessageViewModel",
        "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer",  # purchase
        "liveChatSponsorshipsGiftRedemptionAnnouncementRenderer",  # receive
        "liveChatSponsorshipsHeaderRenderer",
    ],
}

_KNOWN_REPLACE_ACTION_TYPES = {
    "replaceChatItemAction": [
        "liveChatPlaceholderItemRenderer",
        "liveChatTextMessageRenderer",
    ],
}

# actions that have an 'item'
_KNOWN_ITEM_ACTION_TYPES = {
    **_KNOWN_ADD_TICKER_TYPES,
    **_KNOWN_ADD_ACTION_TYPES,
}

# [message deleted] or [message retracted]
_KNOWN_REMOVE_ACTION_TYPES = {
    "removeChatItemAction": [
        "banUser",
    ],
    "removeChatItemByAuthorAction": [
        "banUser",
    ],
    "markChatItemsByAuthorAsDeletedAction": ["banUser"],  # deletedStateMessage
    "markChatItemAsDeletedAction": ["deletedMessage"],  # deletedStateMessage
}

_KNOWN_ADD_BANNER_TYPES = {
    "addBannerToLiveChatCommand": [
        "liveChatBannerRenderer",
        "liveChatBannerHeaderRenderer",
        "liveChatBannerChatSummaryRenderer",
        "liveChatTextMessageRenderer",
    ],
}

_KNOWN_REMOVE_BANNER_TYPES = {
    "removeBannerForLiveChatCommand": ["removeBanner"],  # targetActionId
}

_KNOWN_TOOLTIP_ACTION_TYPES = {
    "showLiveChatTooltipCommand": ["tooltipRenderer"]
}

_KNOWN_POLL_ACTION_TYPES: dict[str, list[str]] = {
    "showLiveChatActionPanelAction": ["pollRenderer"],
    "updateLiveChatPollAction": ["pollRenderer"],
    "closeLiveChatActionPanelAction": ["pollClosedEvent"],
}

_KNOWN_IGNORE_ACTION_TYPES: dict[str, list[str]] = {
    "addInteractivityWidgetAction": [],
    "liveChatReportModerationStateCommand": [],
    "showCreatorGoalTickerChipCommand": [],
}

_KNOWN_ACTION_TYPES = {
    **_KNOWN_ITEM_ACTION_TYPES,
    **_KNOWN_REMOVE_ACTION_TYPES,
    **_KNOWN_REPLACE_ACTION_TYPES,
    **_KNOWN_ADD_BANNER_TYPES,
    **_KNOWN_REMOVE_BANNER_TYPES,
    **_KNOWN_TOOLTIP_ACTION_TYPES,
    **_KNOWN_POLL_ACTION_TYPES,
    **_KNOWN_IGNORE_ACTION_TYPES,
}
