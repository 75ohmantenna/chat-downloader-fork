# SPDX-License-Identifier: MIT

"""YouTube Chat Downloader Extractor Module.

This module contains the main YouTubeChatDownloader class that orchestrates all
YouTube chat downloading functionality by using the client, parsing, and
constants modules.

This module contains the orchestrating class with tests.
"""

from typing import Any

from chat_downloader.sites.base import BaseChatDownloader

from .chat_streams import YouTubeChatStreamsMixin
from .chat_users_retrieval import YouTubeChatUsersRetrievalMixin
from .chat_users_router import YouTubeChatUsersRouterMixin
from .client_auth import (
    _get_sid_cookies,
    _initialize_consent,
    _initialize_pref,
)
from .constants_patterns import _VALID_URLS
from .discovery_channels import YouTubeChannelDiscoveryMixin
from .discovery_helpers import YouTubeDiscoveryHelpersMixin
from .discovery_playlists import YouTubePlaylistDiscoveryMixin
from .helpers import (
    _extract_browse_continuation_token_from_response,  # noqa: F401
)
from .video_initialization import YouTubeVideoInitializationMixin
from .video_metadata import YouTubeVideoMetadataCoreMixin


class YouTubeChatDownloader(
    YouTubeDiscoveryHelpersMixin,
    YouTubeChannelDiscoveryMixin,
    YouTubePlaylistDiscoveryMixin,
    YouTubeVideoMetadataCoreMixin,
    YouTubeVideoInitializationMixin,
    YouTubeChatStreamsMixin,
    YouTubeChatUsersRouterMixin,
    YouTubeChatUsersRetrievalMixin,
    BaseChatDownloader,
):
    """Main class for downloading YouTube chat messages."""

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the YouTube session cookies required by chat pages."""
        super().__init__(**kwargs)
        _initialize_pref(self)
        _initialize_consent(self)

    @property
    def _has_auth_cookies(self) -> bool:
        """Check if YouTube authentication cookies are present.

        YouTube authentication requires LOGIN_INFO cookie plus at least one
        SAPISID variant (SAPISID, __Secure-1PAPISID, or __Secure-3PAPISID).

        :return: True if auth cookies are present, False otherwise
        :rtype: bool
        """
        has_login_info = bool(self.get_cookie_value("LOGIN_INFO"))
        yt_sapisid, yt_1psapisid, yt_3psapisid = _get_sid_cookies(self)
        return has_login_info and bool(
            yt_sapisid or yt_1psapisid or yt_3psapisid
        )

    _NAME = "youtube.com"

    _SITE_DEFAULT_PARAMS = {
        "format": "youtube",
    }

    _VALID_URLS = _VALID_URLS
