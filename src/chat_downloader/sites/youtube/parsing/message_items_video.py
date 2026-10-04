# SPDX-License-Identifier: MIT

"""YouTube video item parsing primitives."""

from __future__ import annotations

import re
from typing import Any

from chat_downloader.sites.remap import (
    Remapper as r,  # noqa: N813 — compact table-construction alias; used as r("key", ...) throughout remapping tables
)
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.json_types import JSONDict, dig, get_dict, get_list, get_str

_COMPACT_VIEW_COUNT = re.compile(r"[\d,.]+\s*[KMB]?", re.IGNORECASE)


def _parse_lockup_badge_style(lockup: JSONDict) -> str | None:
    """Return a video type style from a modern lockup thumbnail badge."""
    overlays = multi_get(
        lockup,
        "contentImage",
        "thumbnailViewModel",
        "overlays",
    )
    for overlay in overlays or []:
        badges = multi_get(overlay, "thumbnailBottomOverlayViewModel", "badges")
        for badge in badges or []:
            badge_model = badge.get("thumbnailBadgeViewModel", {})
            text = (badge_model.get("text") or "").upper()
            image_name = (
                multi_get(
                    badge_model,
                    "icon",
                    "sources",
                    0,
                    "clientResource",
                    "imageName",
                )
                or ""
            ).upper()
            if text == "LIVE" or image_name == "LIVE":
                return "LIVE"
            if text in {"UPCOMING", "PREMIERE"}:
                return "UPCOMING"
    return None


def _lockup_view_count(metadata: JSONDict) -> JSONDict:
    """Find view metadata before falling back to an unlabeled legacy first part."""
    from .message_content_text_parser import _parse_text

    model = get_dict(get_dict(metadata, "metadata"), "contentMetadataViewModel")
    parts = [
        part
        for row in get_list(model, "metadataRows")
        if isinstance(row, dict)
        for part in get_list(row, "metadataParts")
        if isinstance(part, dict)
    ]
    fallback = get_dict(next(iter(parts), {}), "text")
    if get_list(fallback, "commandRuns") or not _COMPACT_VIEW_COUNT.fullmatch(
        (_parse_text(fallback) or "").strip()
    ):
        fallback = {}
    for part in parts:
        text = get_dict(part, "text")
        label = get_str(part, "accessibilityLabel") or _parse_text(text) or ""
        if get_str(
            get_dict(part, "leadingIcon"), "name"
        ) == "PLAY_ARROW_OUTLINED" or label.casefold().endswith(("views", "watching")):
            return text
    return fallback


def _lockup_view_model_to_video_renderer(
    lockup: JSONDict,
) -> JSONDict:
    """Convert YouTube's modern lockup view model into videoRenderer shape."""
    metadata = get_dict(get_dict(lockup, "metadata"), "lockupMetadataViewModel")
    view_count = _lockup_view_count(metadata)

    video_renderer: JSONDict = {
        "videoId": get_str(lockup, "contentId")
        or multi_get(
            lockup,
            "rendererContext",
            "commandContext",
            "onTap",
            "innertubeCommand",
            "watchEndpoint",
            "videoId",
        ),
        "title": multi_get(metadata, "title") or {},
    }
    if view_count:
        video_renderer["viewCountText"] = view_count
        video_renderer["shortViewCountText"] = view_count

    badge_style = _parse_lockup_badge_style(lockup)
    if badge_style:
        video_renderer["thumbnailOverlays"] = [
            {
                "thumbnailOverlayTimeStatusRenderer": {
                    "style": badge_style,
                },
            },
        ]

    return video_renderer


def _shorts_view_model_to_video_renderer(shorts: JSONDict) -> JSONDict:
    """Adapt a Shorts model without treating opaque entity IDs as video IDs."""
    endpoint = dig(shorts, "onTap", "innertubeCommand", "reelWatchEndpoint")
    metadata = get_dict(shorts, "overlayMetadata")
    return {
        "videoId": get_str(endpoint if isinstance(endpoint, dict) else {}, "videoId"),
        "title": get_dict(metadata, "primaryText"),
        "viewCountText": get_dict(metadata, "secondaryText"),
        "shortViewCountText": get_dict(metadata, "secondaryText"),
    }


def _parse_video(video_renderer: JSONDict) -> dict[str, Any]:
    """Parse video information from a YouTube video renderer."""
    from chat_downloader.sites.youtube.constants_message import (
        build_video_remapping,
    )

    if "lockupViewModel" in video_renderer:
        video_renderer = _lockup_view_model_to_video_renderer(
            video_renderer["lockupViewModel"]  # type: ignore[arg-type]
        )
    elif "shortsLockupViewModel" in video_renderer:
        video_renderer = _shorts_view_model_to_video_renderer(
            get_dict(video_renderer, "shortsLockupViewModel")
        )

    # Get video type:
    # One of DEFAULT, UPCOMING, LIVE
    video_type = "DEFAULT"
    thumbnail_overlays = multi_get(video_renderer, "thumbnailOverlays") or []
    for thumbnail_overlay in thumbnail_overlays:
        video_type = multi_get(
            thumbnail_overlay,
            "thumbnailOverlayTimeStatusRenderer",
            "style",
        )
        if video_type:
            break

    video_renderer["videoType"] = video_type

    _video_remapping = build_video_remapping()
    return r.remap_dict(video_renderer, _video_remapping)
