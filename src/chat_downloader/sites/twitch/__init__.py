# SPDX-License-Identifier: MIT

"""Twitch chat downloader package.

This package provides Twitch chat downloading functionality through focused
metadata, live IRC, replay, and parsing modules:

- extractor.py: Main TwitchChatDownloader class and site entry points.
- live_service.py and irc_transport.py: live chat orchestration and IRC.
- replay_service.py and replay_transport.py: VOD/clip chat replay retrieval.
- graphql_client.py: Twitch GraphQL metadata and persisted-query requests.
- badge_client.py: Twitch badge retrieval, fallback, and normalization.
- parsing/: message, tag, badge, emote, and system-event parsing.
"""

from __future__ import annotations

from .extractor import TwitchChatDownloader, TwitchError

__all__ = ["TwitchChatDownloader", "TwitchError"]
