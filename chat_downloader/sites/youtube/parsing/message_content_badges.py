# SPDX-License-Identifier: MIT

"""Badge and currency parsing helpers for YouTube messages."""

import re
from typing import Any

from chat_downloader.sites.models import Image

from .message_links import _get_source_image_url


def _parse_badges(badge_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse badge information for chat authors."""
    from .message_items_content_parser import _parse_item

    badges = []

    for badge in badge_items:
        to_add = {}
        parsed_badge = _parse_item(badge)

        title = parsed_badge.pop("tooltip", None)
        if title:
            to_add["title"] = title

        icon = parsed_badge.pop("icon", None)
        if icon:
            to_add["icon_name"] = icon.lower()

        badge_icons = parsed_badge.pop("badge_icons", None)
        if badge_icons:
            to_add["icons"] = []

            url = None
            for icon in badge_icons:
                url = icon.get("url")
                if url:
                    matches = re.search(r"=s(\d+)", url)
                    if matches:
                        size = int(matches.group(1))
                        to_add["icons"].append(Image(url, size, size).json())
            if url:
                to_add["icons"].insert(
                    0,
                    Image(_get_source_image_url(url), image_id="source").json(),
                )

        badges.append(to_add)

        # if 'member'
        # remove the tooltip afterwards
    return badges


def _parse_currency(item: dict[str, Any]) -> dict[str, Any]:
    """Parse currency/monetary information from YouTube data."""
    from chat_downloader.sites.youtube.constants_message import (
        _CURRENCY_SYMBOLS,
    )

    mixed_text = item.get("simpleText") or str(item)

    info = re.split(r"([\d,\.]+)", mixed_text)
    if len(info) >= 2:  # Correct parse
        currency_symbol = info[0].strip()
        currency_code = _CURRENCY_SYMBOLS.get(currency_symbol, currency_symbol)
        amount = float(info[1].replace(",", ""))

    else:  # Unable to get info
        amount = float(re.sub(r"[^\d\.]+", "", mixed_text))
        currency_symbol = currency_code = None

    return {
        "text": mixed_text,
        "amount": amount,
        "currency": currency_code,  # ISO_4217
        "currency_symbol": currency_symbol,
    }
