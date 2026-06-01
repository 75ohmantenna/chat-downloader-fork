# SPDX-License-Identifier: MIT

"""YouTube chat action parsing handlers."""

from typing import Any

from chat_downloader.debugging import debug_log
from chat_downloader.utils.dict_utils import multi_get, try_get_first_key

from .message_content_text_parser import _parse_runs
from .messages import _parse_item


def _handle_item_action(
    action: dict[str, Any],
    original_action_type: str,
    data: dict[str, Any],
    offset: float,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """Handle add item and ticker actions."""
    original_item = multi_get(action, original_action_type, "item")
    original_message_type = try_get_first_key(original_item)
    data = _parse_item(original_item, data, offset)
    return (data, original_item, original_message_type, original_action_type)


def _handle_remove_action(
    action: dict[str, Any],
    original_action_type: str,
    data: dict[str, Any],
    offset: float,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """Handle remove/delete/ban actions."""
    original_item = action
    if original_action_type == "markChatItemAsDeletedAction":
        original_message_type = "deletedMessage"
    else:
        original_message_type = "banUser"
    data = _parse_item(original_item, data, offset)
    return (data, original_item, original_message_type, original_action_type)


def _handle_replace_action(
    action: dict[str, Any],
    original_action_type: str,
    data: dict[str, Any],
    offset: float,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """Handle message replacement actions."""
    original_item = multi_get(action, original_action_type, "replacementItem")
    original_message_type = try_get_first_key(original_item)
    data = _parse_item(original_item, data, offset)
    return (data, original_item, original_message_type, original_action_type)


def _handle_tooltip_action(
    action: dict[str, Any],
    original_action_type: str,
    data: dict[str, Any],
    offset: float,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """Handle tooltip display actions."""
    original_item = multi_get(action, original_action_type, "tooltip")
    original_message_type = try_get_first_key(original_item)
    data = _parse_item(original_item, data, offset)
    return (data, original_item, original_message_type, original_action_type)


def _handle_add_banner_action(
    action: dict[str, Any],
    original_action_type: str,
    data: dict[str, Any],
    offset: float,
) -> tuple[dict[str, Any], dict[str, Any], str | None, str]:
    """Handle add banner actions."""
    original_item = multi_get(action, original_action_type, "bannerRenderer")
    if original_item:
        original_message_type = try_get_first_key(original_item)
        contents = original_item[original_message_type].get("contents")
        parsed_contents = _parse_item(contents, offset=offset)
        data.update(parsed_contents)
    else:
        debug_log(
            "No bannerRenderer item", f"Action type: {original_action_type}"
        )
        original_message_type = None
    return (data, original_item, original_message_type, original_action_type)


def _handle_remove_banner_action(
    action: dict[str, Any],
    original_action_type: str,
    data: dict[str, Any],
    offset: float,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """Handle remove banner actions."""
    original_item = action
    original_message_type = "removeBanner"
    data = _parse_item(original_item, data, offset)
    return (data, original_item, original_message_type, original_action_type)


def _handle_poll_action(
    action: dict[str, Any],
    original_action_type: str,
    data: dict[str, Any],
    offset: float,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """Handle poll create, update, and close actions."""
    if original_action_type == "closeLiveChatActionPanelAction":
        action_data = action.get("closeLiveChatActionPanelAction") or {}
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
            "text": (
                _parse_runs(c["text"]).get("message") if c.get("text") else None
            ),
            "vote_ratio": c.get("voteRatio"),
            "selected": c.get("selected", False),
        }
        for c in (poll_renderer.get("choices") or [])
    ]

    return (data, action, "pollRenderer", original_action_type)
