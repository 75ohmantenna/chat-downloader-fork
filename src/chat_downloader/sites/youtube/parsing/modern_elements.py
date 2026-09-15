# SPDX-License-Identifier: MIT

"""Normalize observed mobile chat models, including nested paid ticker items."""

from __future__ import annotations

from chat_downloader.utils.json_types import JSONDict, dig, get_dict, get_str

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


_MODELS = {
    "liveChatTextMessageModel": ("liveChatTextMessageRenderer", _text_message),
    "superChatItemModel": ("liveChatPaidMessageRenderer", _paid_message),
    "liveChatPaidStickerModel": ("liveChatPaidStickerRenderer", _paid_sticker),
    "viewerEngagementMessageModel": (
        "liveChatViewerEngagementMessageRenderer",
        _engagement,
    ),
}


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
    compatibility = get_dict(element, "compatibilityOptions")
    for source, target in (
        ("liveChatId", "id"),
        ("liveChatAuthorExternalChannelId", "authorExternalChannelId"),
    ):
        value = get_str(compatibility, source)
        if value:
            renderer[target] = value
    return {renderer_name: renderer}
