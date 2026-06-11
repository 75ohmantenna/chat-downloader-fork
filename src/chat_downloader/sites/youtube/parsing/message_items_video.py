# SPDX-License-Identifier: MIT

"""YouTube video item parsing primitives."""

from __future__ import annotations

from typing import Any

from chat_downloader.sites.remap import (
    Remapper as r,  # noqa: N813 — compact table-construction alias; used as r("key", ...) throughout remapping tables
)
from chat_downloader.utils.dict_utils import multi_get


def _parse_lockup_badge_style(lockup: dict[str, Any]) -> str | None:
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


def _lockup_view_model_to_video_renderer(
    lockup: dict[str, Any],
) -> dict[str, Any]:
    """Convert YouTube's modern lockup view model into videoRenderer shape."""
    metadata = multi_get(lockup, "metadata", "lockupMetadataViewModel") or {}
    metadata_rows = multi_get(
        metadata,
        "metadata",
        "contentMetadataViewModel",
        "metadataRows",
    )
    metadata_parts = multi_get(metadata_rows or [], 0, "metadataParts") or []
    view_count = multi_get(metadata_parts, 0, "text")

    video_renderer: dict[str, Any] = {
        "videoId": lockup.get("contentId")
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


def _parse_video(video_renderer: dict[str, Any]) -> dict[str, Any]:
    """Parse video information from a YouTube video renderer."""
    from chat_downloader.sites.youtube.constants_message import (
        build_video_remapping,
    )

    if "lockupViewModel" in video_renderer:
        video_renderer = _lockup_view_model_to_video_renderer(
            video_renderer["lockupViewModel"]
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
