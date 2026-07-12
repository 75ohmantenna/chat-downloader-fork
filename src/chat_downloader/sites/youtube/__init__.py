# SPDX-License-Identifier: MIT

"""YouTube chat downloader package.

This package provides YouTube chat downloading functionality through focused
bootstrap, request, continuation, parsing, and discovery modules:

- extractor.py: Main YouTubeChatDownloader class and site entry points.
- video_initialization.py, video_metadata.py, and video_status.py: watch-page
  bootstrap, metadata, and playability state.
- client_context.py, client_requests_initial.py, and
  client_requests_continuation.py: request construction and InnerTube calls.
- chat_streams.py: live/replay stream entry points (mixin). continuation.py:
  the continuation loop (_ContinuationLoop). continuation_helpers.py and
  continuations.py: pure loop helpers and the response parser.
- parsing/: action routing and message normalization.
"""

from __future__ import annotations

from .extractor import YouTubeChatDownloader

__all__ = ["YouTubeChatDownloader"]
