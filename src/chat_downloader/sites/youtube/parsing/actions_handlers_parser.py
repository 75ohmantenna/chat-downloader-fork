# SPDX-License-Identifier: MIT

"""YouTube chat action parsing handlers."""

from __future__ import annotations

from chat_downloader.debugging import debug_log
from chat_downloader.sites.youtube.constants_actions_messages_core import (
    _PATH_BANNER_RENDERER,
    _PATH_ITEM,
    _PATH_REPLACEMENT_ITEM,
    _PATH_TOOLTIP,
    _RENDERER_BANNER_CHAT_SUMMARY,
)
from chat_downloader.utils.dict_utils import multi_get, try_get_first_key
from chat_downloader.utils.json_types import (
    JSONDict,
    get_dict,
    get_int,
    get_list,
    get_str,
)

from .message_content_text_parser import _parse_runs
from .message_items_content_parser import _normalize_modern_element_item, _parse_item


def _handle_item_action(
    action: JSONDict,
    original_action_type: str,
    data: JSONDict,
    offset: float,
) -> tuple[JSONDict, JSONDict, str, str]:
    """Handle add item and ticker actions."""
    original_item = multi_get(action, original_action_type, _PATH_ITEM)
    original_item = _normalize_modern_element_item(original_item)
    original_message_type = try_get_first_key(original_item)
    data = _parse_item(original_item, data, offset)
    return (data, original_item, original_message_type, original_action_type)


def _image_sources_as_thumbnails(image: JSONDict) -> JSONDict:
    """Adapt a modern image source list for the shared thumbnail parser."""
    sources = get_list(image, "sources")
    return {"thumbnails": sources} if sources else {}


def _handle_interactivity_widget_action(
    action: JSONDict,
    original_action_type: str,
    data: JSONDict,
    offset: float,
) -> tuple[JSONDict, JSONDict, str | None, str]:
    """Normalize a Jewels gift widget into the existing gift message shape."""
    action_data = get_dict(action, original_action_type)
    widget_renderer = get_dict(action_data, "widgetRenderer")
    widget = get_dict(widget_renderer, "interactivityWidgetRenderer")
    content = get_dict(widget, "content")
    attribution = get_dict(content, "giftAttributionItemViewModel")
    if not attribution:
        return (data, {}, None, original_action_type)

    element = get_dict(attribution, "elementRenderer")
    compatibility = get_dict(element, "compatibilityOptions")
    gift_item: JSONDict = {
        "id": get_str(attribution, "id") or get_str(compatibility, "liveChatId"),
        "authorExternalChannelId": get_str(
            compatibility,
            "liveChatAuthorExternalChannelId",
        ),
        "authorName": get_dict(attribution, "authorName"),
        "text": get_dict(attribution, "detailText"),
        "comboCount": get_int(attribution, "comboCount"),
    }

    gift_label = get_str(attribution, "giftA11yLabel")
    if gift_label:
        gift_item["giftImageA11yLabel"] = gift_label

    author_avatar = get_dict(attribution, "authorAvatar")
    avatar_view_model = get_dict(author_avatar, "avatarViewModel")
    author_photo = _image_sources_as_thumbnails(get_dict(avatar_view_model, "image"))
    if author_photo:
        gift_item["authorPhoto"] = author_photo

    gift_image = _image_sources_as_thumbnails(get_dict(attribution, "attributionImage"))
    if gift_image:
        gift_item["giftImage"] = gift_image

    original_item: JSONDict = {"giftMessageViewModel": gift_item}
    data = _parse_item(original_item, data, offset)
    return (
        data,
        original_item,
        "giftMessageViewModel",
        original_action_type,
    )


def _handle_remove_action(
    action: JSONDict,
    original_action_type: str,
    data: JSONDict,
    offset: float,
) -> tuple[JSONDict, JSONDict, str, str]:
    """Handle remove/delete/ban actions."""
    original_item = action
    if original_action_type == "markChatItemAsDeletedAction":
        original_message_type = "deletedMessage"
    else:
        original_message_type = "banUser"
    data = _parse_item(original_item, data, offset)
    return (data, original_item, original_message_type, original_action_type)


def _handle_replace_action(
    action: JSONDict,
    original_action_type: str,
    data: JSONDict,
    offset: float,
) -> tuple[JSONDict, JSONDict, str, str]:
    """Handle message replacement actions."""
    original_item = multi_get(action, original_action_type, _PATH_REPLACEMENT_ITEM)
    original_message_type = try_get_first_key(original_item)
    data = _parse_item(original_item, data, offset)
    return (data, original_item, original_message_type, original_action_type)


def _handle_tooltip_action(
    action: JSONDict,
    original_action_type: str,
    data: JSONDict,
    offset: float,
) -> tuple[JSONDict, JSONDict, str, str]:
    """Handle tooltip display actions."""
    original_item = multi_get(action, original_action_type, _PATH_TOOLTIP)
    original_message_type = try_get_first_key(original_item)
    data = _parse_item(original_item, data, offset)
    return (data, original_item, original_message_type, original_action_type)


def _handle_add_banner_action(
    action: JSONDict,
    original_action_type: str,
    data: JSONDict,
    offset: float,
) -> tuple[JSONDict, JSONDict, str | None, str]:
    """Handle add banner actions."""
    original_item = multi_get(action, original_action_type, _PATH_BANNER_RENDERER)
    if original_item:
        original_message_type = try_get_first_key(original_item)
        contents = original_item[original_message_type].get("contents")
        content_message_type = try_get_first_key(contents)
        parsed_contents = _parse_item(contents, offset=offset)
        data.update(parsed_contents)
        if content_message_type == _RENDERER_BANNER_CHAT_SUMMARY:
            original_item = contents
            original_message_type = content_message_type
    else:
        debug_log("No bannerRenderer item", f"Action type: {original_action_type}")
        original_message_type = None
    return (data, original_item, original_message_type, original_action_type)


def _handle_remove_banner_action(
    action: JSONDict,
    original_action_type: str,
    data: JSONDict,
    offset: float,
) -> tuple[JSONDict, JSONDict, str, str]:
    """Handle remove banner actions."""
    original_item = action
    original_message_type = "removeBanner"
    data = _parse_item(original_item, data, offset)
    return (data, original_item, original_message_type, original_action_type)


def _handle_poll_action(
    action: JSONDict,
    original_action_type: str,
    data: JSONDict,
    offset: float,  # noqa: ARG001 — uniform action-handler callable signature
) -> tuple[JSONDict, JSONDict, str, str]:
    """Handle poll create, update, and close actions."""
    if original_action_type == "closeLiveChatActionPanelAction":
        action_data = get_dict(action, "closeLiveChatActionPanelAction")
        data["poll_id"] = action_data.get("targetPanelId")
        return (data, action, "pollClosedEvent", original_action_type)

    if original_action_type == "showLiveChatActionPanelAction":
        panel = multi_get(
            action,
            "showLiveChatActionPanelAction",
            "panelToShow",
            "liveChatActionPanelRenderer",
        )
        poll_renderer = multi_get(panel or {}, "contents", "pollRenderer") or {}
        if panel:
            data["poll_id"] = panel.get("id")
    else:
        poll_renderer = (
            multi_get(
                action,
                "updateLiveChatPollAction",
                "pollToUpdate",
                "pollRenderer",
            )
            or {}
        )

    if poll_renderer.get("liveChatPollId"):
        data["poll_id"] = poll_renderer["liveChatPollId"]

    header = multi_get(poll_renderer, "header", "pollHeaderRenderer") or {}
    question = header.get("pollQuestion")
    if question:
        data["poll_question"] = _parse_runs(question).get("message")

    data["poll_choices"] = [
        {
            "text": (_parse_runs(c["text"]).get("message") if c.get("text") else None),
            "vote_ratio": c.get("voteRatio"),
            "selected": c.get("selected", False),
        }
        for c in (poll_renderer.get("choices") or [])
    ]

    return (data, action, "pollRenderer", original_action_type)
