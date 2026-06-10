# SPDX-License-Identifier: MIT

"""YouTube chat user/channel lookup implementations."""

from __future__ import annotations

from itertools import islice
from typing import TYPE_CHECKING, Any, cast

from chat_downloader.debugging import log
from chat_downloader.errors import ChatDownloaderError
from chat_downloader.sites.models import Chat
from chat_downloader.utils.dict_utils import try_get_first_value

if TYPE_CHECKING:
    from collections.abc import Iterator

    from chat_downloader.models import ChatRequest

    from ._protocols import YouTubeDownloaderProto


def _copy_chat_metadata(chat_item: Chat, chat: Any) -> None:
    """Copy public chat attributes onto the placeholder chat item."""
    for key, value in vars(chat).items():
        if key != "chat" and not key.startswith("_"):
            setattr(chat_item, key, value)


class YouTubeChatUsersRetrievalMixin:
    """Methods for performing user-based chat listing and retrieval."""

    def _get_chat_by_user_args(
        self,
        user_video_args: dict[str, str],
        params: ChatRequest,
    ) -> Chat:
        """Get chat by user arguments."""
        title = try_get_first_value(user_video_args)
        chat_item = Chat(title=title, id=title)  # Create empty chat object
        chat_item.chat = self._get_chat_messages_by_user_args(
            user_video_args,
            chat_item,
            params,
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
        # For efficiency purposes, do not loop over all past broadcasts if not
        # found
        max_vids_to_try = 5

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
                        f'Skipping video with ID: "{video_id}" '
                        "(not live/upcoming)",
                    )
                    continue

                if video_id in list_of_vids_to_ignore:
                    log("debug", f'Skipping video with ID: "{video_id}"')
                    continue

                try:
                    chat = cast(
                        "YouTubeDownloaderProto", self
                    ).get_chat_by_video_id(video_id, params)

                    log(
                        "info",
                        f'Found a livestream: "{video["title"]}" ({video_id}).',
                    )

                    _copy_chat_metadata(chat_item, chat)
                    yield from chat
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
