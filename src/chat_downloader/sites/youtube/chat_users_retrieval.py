# SPDX-License-Identifier: MIT

"""YouTube chat user/channel lookup implementations."""

from __future__ import annotations

from itertools import islice
from typing import TYPE_CHECKING, Any, cast

from chat_downloader.debugging import log
from chat_downloader.errors import ChatDownloaderError
from chat_downloader.models import coerce_chat_request
from chat_downloader.sites.models import Chat
from chat_downloader.utils.dict_utils import try_get_first_value
from chat_downloader.utils.json_types import get_int

from .capture_inspection import inspect_capture
from .delivery_recovery import ChannelDeliveryHistory, ChildDiagnostics

if TYPE_CHECKING:
    from collections.abc import Iterator

    from chat_downloader.models import ChatRequest

    from ._protocols import YouTubeDownloaderProto


def _copy_chat_metadata(chat_item: Chat, chat: Any) -> None:
    """Copy discovered metadata without replacing configured runtime state."""
    for key, value in vars(chat).items():
        if key not in {"chat", "site", "diagnostics"} and not key.startswith("_"):
            setattr(chat_item, key, value)
    chat_item._metadata_pending = False


class YouTubeChatUsersRetrievalMixin:
    """Methods for performing user-based chat listing and retrieval."""

    def _get_chat_by_user_args(
        self,
        user_video_args: dict[str, str],
        params: ChatRequest | dict[str, object],
    ) -> Chat:
        """Get chat by user arguments."""
        request = coerce_chat_request(params)
        title = try_get_first_value(user_video_args)
        chat_item = Chat(title=title, id=title)
        chat_item._metadata_pending = True
        chat_item._capture_inspector = lambda path, summary: inspect_capture(
            path, run_summary=summary
        )
        chat_item.chat = self._get_chat_messages_by_user_args(
            user_video_args,
            chat_item,
            request,
        )

        return chat_item

    def _get_chat_messages_by_user_args(
        self,
        user_video_args: dict[str, str],
        chat_item: Chat,
        params: ChatRequest,
    ) -> Iterator[Any]:
        """Generate chat messages by user arguments."""
        list_of_vids_to_ignore = params.ignore or []

        sleep_amount = 30
        # Bound each discovery pass before refreshing live/upcoming results.
        max_vids_to_try = 5
        deliveries = ChannelDeliveryHistory()

        while True:
            vids = cast("YouTubeDownloaderProto", self).get_user_videos(
                **user_video_args,
                video_type="live",
                params=params,
            )

            for video in islice(vids, max_vids_to_try):
                video_id = video["video_id"]

                if video["video_type"] not in ("LIVE", "UPCOMING"):
                    log(
                        "debug",
                        f'Skipping video with ID: "{video_id}" (not live/upcoming)',
                    )
                    continue

                if video_id in list_of_vids_to_ignore:
                    log("debug", f'Skipping video with ID: "{video_id}"')
                    continue

                try:
                    chat = cast("YouTubeDownloaderProto", self).get_chat_by_video_id(
                        video_id, params
                    )

                    log(
                        "info",
                        f'Found a livestream: "{video["title"]}" ({video_id}).',
                    )

                    _copy_chat_metadata(chat_item, chat)
                    overlap = deliveries.begin(video_id)
                    diagnostics = ChildDiagnostics(chat_item.diagnostics)
                    diagnostics.update(chat.diagnostics)
                    try:
                        for item in chat:
                            diagnostics.update(chat.diagnostics)
                            if overlap.accept(item):
                                yield item
                            else:
                                key = "channel_overlap_suppressed_count"
                                chat_item.diagnostics[key] = (
                                    get_int(chat_item.diagnostics, key) + 1
                                )
                    finally:
                        try:
                            chat.close()
                        finally:
                            diagnostics.update(chat.diagnostics)
                    break

                except ChatDownloaderError as e:
                    log(
                        "warning",
                        f'Unable to get chat for "{video["title"]}" '
                        f'({video_id}) due to an error: "{e}"',
                    )

            log(
                "info",
                "There are no active or upcoming livestreams with a live "
                f"chat. Retrying in {sleep_amount} seconds.",
            )
            from chat_downloader.utils.timed_generator import polling_sleep

            polling_sleep(sleep_amount)
