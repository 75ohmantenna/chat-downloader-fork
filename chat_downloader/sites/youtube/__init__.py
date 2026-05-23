# SPDX-License-Identifier: MIT

"""YouTube chat downloader package.

This package provides YouTube chat downloading functionality through focused
bootstrap, request, continuation, parsing, and discovery modules:

- extractor.py: Main YouTubeChatDownloader class and site entry points.
- video_initialization.py, video_metadata.py, and video_status.py: watch-page
  bootstrap, metadata, and playability state.
- client_context.py, client_requests_initial.py, and
  client_requests_continuation.py: request construction and InnerTube calls.
- chat_streams.py and chat_streams_runtime_iteration.py: live/replay stream
  orchestration.
- parsing/: action routing and message normalization.
"""

from .extractor import YouTubeChatDownloader

__all__ = ["YouTubeChatDownloader"]
