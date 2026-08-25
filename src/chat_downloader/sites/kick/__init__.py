# SPDX-License-Identifier: MIT

"""Kick.com site package for unauthenticated live and VOD chat."""

from __future__ import annotations

from .errors import KickError
from .extractor import KickChatDownloader

__all__ = ["KickChatDownloader", "KickError"]
