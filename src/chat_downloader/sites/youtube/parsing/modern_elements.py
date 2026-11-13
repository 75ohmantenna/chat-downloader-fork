# SPDX-License-Identifier: MIT

"""Normalize observed mobile chat models, including nested paid ticker items."""

from __future__ import annotations

from chat_downloader.utils.json_types import (
    JSONDict,
    JSONList,
    dig,
    get_dict,
    get_int,
    get_list,
    get_str,
)

from .modern_text import attributed_text, author_badges, image_thumbnails


def _text_message(model: JSONDict) -> JSONDict:
    data = get_dict(model, "messageData")
    attributed = get_dict(data, "attributedTextData")
    if not attributed:
        return {}
    name = get_str(get_dict(attributed, "authorName"), "content").strip()
    fields: JSONDict = {
        "authorName": {"simpleText": name} if name else {},
        "message": attributed_text(get_dict(attributed, "contentText")),
        "authorPhoto": image_thumbnails(
            get_dict(get_dict(data, "authorAvatar"), "image")
        ),
        "authorBadges": author_badges(attributed),
    }
    return {key: value for key, value in fields.items() if value}


def _paid_author(data: JSONDict) -> JSONDict:
    name = data.get("authorName")
    if not isinstance(name, str):
        text = get_dict(data, "authorName")
        name = get_str(text, "content") or get_str(text, "simpleText")
    fields: JSONDict = {
        "authorName": {"simpleText": name} if name else {},
        "authorPhoto": image_thumbnails(
            get_dict(get_dict(data, "authorPhoto"), "image")
        ),
        "authorBadges": author_badges(get_dict(data, "authorNameData")),
    }
    return {key: value for key, value in fields.items() if value}


def _banner_message(model: JSONDict) -> JSONDict:
    """Require pinned banner content before accepting the mobile model."""
    fields = _text_message(model)
    return fields if fields.get("message") else {}


def _summary_banner(model: JSONDict) -> JSONDict:
    """Normalize the observed mobile summary without its UI command payloads."""
    summary = get_dict(model, "liveChatBannerChatSummaryRenderer")
    text = attributed_text(
        get_dict(get_dict(summary, "chatSummary"), "elementsAttributedString")
    )
    if not text:
        return {}
    return {
        "liveChatSummaryId": get_str(summary, "liveChatSummaryId"),
        "chatSummary": text,
    }


def _paid_message(model: JSONDict) -> JSONDict:
    data = get_dict(model, "paidMessageData")
    if not data:
        return {}
    renderer = _paid_author(get_dict(data, "paidItemHeaderStaticData"))
    renderer["message"] = attributed_text(get_dict(data, "message"))
    price = get_str(get_dict(data, "paidMessageHeaderData"), "priceText")
    if price.strip():
        renderer["purchaseAmountText"] = {"simpleText": price}
    return {key: value for key, value in renderer.items() if value}


def _paid_sticker(model: JSONDict) -> JSONDict:
    data = get_dict(model, "liveChatPaidSticker")
    if not data:
        return {}
    renderer = _paid_author(data)
    label = get_str(data, "stickerAccessibilityLabel")
    price = get_str(model, "priceText")
    renderer.update(
        {
            "purchaseAmountText": {"simpleText": price} if price.strip() else {},
            "sticker": image_thumbnails(get_dict(data, "sticker")),
            "message": {"runs": [{"text": label}]} if label else {},
        }
    )
    return {key: value for key, value in renderer.items() if value}


def _engagement(model: JSONDict) -> JSONDict:
    message = attributed_text(get_dict(get_dict(model, "data"), "messageText"))
    return {"message": message} if message else {}


def _wrapped_gift_message(model: JSONDict) -> JSONDict:
    """Unwrap the captured gift view model while preserving its native fields."""
    if model.keys() - {"context", "viewModel"} or (
        "context" in model and not isinstance(dig(model, "context"), dict)
    ):
        return {}
    views = get_dict(model, "viewModel")
    if views.keys() != {"giftMessageViewModel"}:
        return {}
    gift = get_dict(views, "giftMessageViewModel")
    if not (
        get_str(gift, "id").strip()
        and get_str(get_dict(gift, "authorName"), "content").strip()
        and get_str(get_dict(gift, "text"), "content").strip()
    ):
        return {}
    return gift.copy()


def _membership_badges(header: JSONDict) -> JSONList:
    """Keep supported badge metadata without passing malformed native fields."""
    badges: JSONList = []
    for item in get_list(header, "authorBadges"):
        if not isinstance(item, dict):
            continue
        badge = get_dict(item, "liveChatAuthorBadgeRenderer")
        fields: JSONDict = {}
        title = get_str(badge, "tooltip")
        if title:
            fields["tooltip"] = title
        icon = get_str(get_dict(badge, "icon"), "iconType")
        if icon:
            fields["icon"] = {"iconType": icon}
        thumbnails = image_thumbnails(
            get_dict(get_dict(badge, "customThumbnail"), "elementsImage")
        )
        if thumbnails:
            fields["customThumbnail"] = thumbnails
        if fields:
            badges.append({"liveChatAuthorBadgeRenderer": fields})
    return badges


def _new_membership(model: JSONDict) -> JSONDict:
    header = get_dict(model, "headerRenderer")
    author = get_dict(get_dict(header, "authorName"), "elementsAttributedString")
    welcome = get_dict(get_dict(header, "primaryText"), "elementsAttributedString")
    if (
        not get_str(author, "content").strip()
        or not get_str(welcome, "content").strip()
    ):
        return {}
    fields: JSONDict = {
        "authorName": {"simpleText": get_str(author, "content")},
        "headerSubtext": attributed_text(welcome),
        "authorPhoto": image_thumbnails(
            get_dict(get_dict(header, "authorPhoto"), "elementsImage")
        ),
        "authorBadges": _membership_badges(header),
    }
    renderer: JSONDict = {key: value for key, value in fields.items() if value}
    color = get_int(model, "backgroundColor", -1)
    if 0 <= color <= 0xFFFFFFFF:
        renderer["backgroundColor"] = color
    return renderer


_MODELS = {
    "liveChatTextMessageModel": ("liveChatTextMessageRenderer", _text_message),
    "liveChatTextMessageBannerModel": ("liveChatTextMessageRenderer", _banner_message),
    "liveChatSummaryBannerModel": (
        "liveChatBannerChatSummaryRenderer",
        _summary_banner,
    ),
    "superChatItemModel": ("liveChatPaidMessageRenderer", _paid_message),
    "liveChatPaidStickerModel": ("liveChatPaidStickerRenderer", _paid_sticker),
    "viewerEngagementMessageModel": (
        "liveChatViewerEngagementMessageRenderer",
        _engagement,
    ),
    "youtubeModel": ("giftMessageViewModel", _wrapped_gift_message),
    "liveChatSponsorshipsNewMemberAnnouncementModel": (
        "liveChatMembershipItemRenderer",
        _new_membership,
    ),
}

_STRICT_RENDERERS = {"giftMessageViewModel", "liveChatMembershipItemRenderer"}


def normalize_element(item: JSONDict) -> JSONDict:
    """Adapt one supported model; leave unknown/malformed items diagnosable.

    Mobile uniqueLoggingIdentifier describes response generation, not original
    message time. Preserve replay-wrapper offsets upstream and omit timestamp
    when the provider does not supply an original message timestamp.
    """
    element = get_dict(item, "elementRenderer")
    models = dig(element, "newElement", "type", "componentType", "model")
    if not isinstance(models, dict) or len(models) != 1:
        return item
    model_name = next(iter(models))
    adapter = _MODELS.get(model_name)
    if adapter is None:
        return item
    renderer_name, adapt = adapter
    renderer = adapt(get_dict(models, model_name))
    if not renderer:
        return item
    if renderer_name in _STRICT_RENDERERS and item.keys() != {"elementRenderer"}:
        return item
    compatibility = get_dict(element, "compatibilityOptions")
    for source, target in (
        ("liveChatId", "id"),
        ("liveChatAuthorExternalChannelId", "authorExternalChannelId"),
    ):
        value = get_str(compatibility, source)
        if value:
            if renderer_name in _STRICT_RENDERERS and get_str(renderer, target) not in {
                "",
                value,
            }:
                return item
            renderer[target] = value
    return {renderer_name: renderer}
