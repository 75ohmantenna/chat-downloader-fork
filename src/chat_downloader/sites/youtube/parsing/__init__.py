# SPDX-License-Identifier: MIT

"""YouTube parsing modules.

Exports message parsing and action handling functions.
"""

from __future__ import annotations

from .actions_handlers_validation import validate_and_finalize_message
from .actions_router import ProcessedAction, process_action
from .message_content_badges import _parse_badges, _parse_currency
from .message_content_text_parser import (
    _get_simple_text,
    _parse_action_button,
    _parse_runs,
    _parse_text,
    _parse_thumbnails,
)
from .message_items_content_parser import _parse_item
from .message_items_video import _parse_video
from .message_links import (
    _get_source_image_url,
    _parse_navigation_endpoint,
    _parse_youtube_link,
)

__all__ = [
    # Action handling
    "ProcessedAction",
    "_get_simple_text",
    "_get_source_image_url",
    "_parse_action_button",
    "_parse_badges",
    "_parse_currency",
    "_parse_item",
    "_parse_navigation_endpoint",
    "_parse_runs",
    "_parse_text",
    "_parse_thumbnails",
    "_parse_video",
    # Message parsing
    "_parse_youtube_link",
    "process_action",
    "validate_and_finalize_message",
]
