# SPDX-License-Identifier: MIT

r"""Normalize Kick subscription and gifted-subscription events.

Handles ``App\Events\SubscriptionEvent`` and
``App\Events\GiftedSubscriptionsEvent`` Pusher payloads.
"""

from __future__ import annotations

import contextlib
from typing import Any

from chat_downloader.errors import ParsingError
from chat_downloader.sites.kick.parsing.messages import _opt_str, _parse_author
from chat_downloader.utils.time_utils import timestamp_to_microseconds


def _parse_subscription_meta(raw_meta: Any) -> dict[str, Any]:
    """Extract structured subscription metadata.

    Args:
        raw_meta: The ``metadata.subscription`` sub-object.

    Returns:
        A dict with ``months``, ``plan``, and ``gift`` when present.
    """
    meta: dict[str, Any] = {}
    if not isinstance(raw_meta, dict):  # pragma: no cover — defensive
        return meta

    months = raw_meta.get("months")
    if isinstance(months, int):
        meta["months"] = months

    plan = _opt_str(raw_meta.get("plan"))
    if plan is not None:
        meta["plan"] = plan

    gift = raw_meta.get("gift")
    if isinstance(gift, bool):
        meta["gift"] = gift

    return meta


def _parse_gifted_meta(raw_meta: Any) -> dict[str, Any]:
    """Extract structured gifted-subscription metadata.

    Args:
        raw_meta: The ``metadata.gifted_subscriptions`` sub-object.

    Returns:
        A dict with ``quantity``, ``plan``, ``gifter_username``, ``recipients``,
        and ``gift`` when present.
    """
    meta: dict[str, Any] = {}
    if not isinstance(raw_meta, dict):  # pragma: no cover — defensive
        return meta

    quantity = raw_meta.get("quantity")
    if isinstance(quantity, int):
        meta["quantity"] = quantity

    plan = _opt_str(raw_meta.get("plan"))
    if plan is not None:
        meta["plan"] = plan

    gifter = _opt_str(raw_meta.get("gifter_username"))
    if gifter is not None:
        meta["gifter_username"] = gifter

    recipients = raw_meta.get("recipients")
    if isinstance(recipients, list):
        meta["recipients"] = list(recipients)

    gift = raw_meta.get("gift")
    if isinstance(gift, bool):
        meta["gift"] = gift

    return meta


def parse_subscription_event(raw: Any) -> dict[str, Any]:
    """Normalize a Kick subscription event.

    Args:
        raw: The decoded ``SubscriptionEvent`` payload.

    Returns:
        A normalized message dictionary with ``message_type`` set to
        ``"subscription"``.

    Raises:
        ParsingError: If ``raw`` is not an object or lacks an ``id``.
    """
    if not isinstance(raw, dict):
        msg = "Kick subscription event payload was not a JSON object."
        raise ParsingError(msg)

    message_id = _opt_str(raw.get("id"))
    if message_id is None:
        msg = "Kick subscription event payload was missing an id."
        raise ParsingError(msg)

    content = raw.get("content")
    info: dict[str, Any] = {
        "message_id": message_id,
        "message_type": "subscription",
        "message": content if isinstance(content, str) else "",
    }

    created_at = raw.get("created_at")
    if isinstance(created_at, str) and created_at:
        with contextlib.suppress(ValueError, TypeError):
            info["timestamp"] = timestamp_to_microseconds(created_at)

    author = _parse_author(raw.get("sender"))
    if author:
        info["author"] = author

    metadata_raw = raw.get("metadata")
    if isinstance(metadata_raw, dict):
        sub_meta = _parse_subscription_meta(metadata_raw.get("subscription"))
        if sub_meta:
            info["metadata"] = sub_meta

    return info


def parse_gifted_subscriptions_event(raw: Any) -> dict[str, Any]:
    """Normalize a Kick gifted-subscriptions event.

    Args:
        raw: The decoded ``GiftedSubscriptionsEvent`` payload.

    Returns:
        A normalized message dictionary with ``message_type`` set to
        ``"gifted_subscriptions"``.

    Raises:
        ParsingError: If ``raw`` is not an object or lacks an ``id``.
    """
    if not isinstance(raw, dict):
        msg = "Kick gifted-subscriptions event payload was not a JSON object."
        raise ParsingError(msg)

    message_id = _opt_str(raw.get("id"))
    if message_id is None:
        msg = "Kick gifted-subscriptions event payload was missing an id."
        raise ParsingError(msg)

    content = raw.get("content")
    info: dict[str, Any] = {
        "message_id": message_id,
        "message_type": "gifted_subscriptions",
        "message": content if isinstance(content, str) else "",
    }

    created_at = raw.get("created_at")
    if isinstance(created_at, str) and created_at:
        with contextlib.suppress(ValueError, TypeError):
            info["timestamp"] = timestamp_to_microseconds(created_at)

    author = _parse_author(raw.get("sender"))
    if author:
        info["author"] = author

    metadata_raw = raw.get("metadata")
    if isinstance(metadata_raw, dict):
        gift_meta = _parse_gifted_meta(metadata_raw.get("gifted_subscriptions"))
        if gift_meta:
            info["metadata"] = gift_meta

    return info
