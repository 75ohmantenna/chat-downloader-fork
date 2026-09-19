# SPDX-License-Identifier: MIT

"""YouTube chat downloader package.

This package provides YouTube chat downloading functionality through focused
bootstrap, request, continuation, parsing, and discovery modules:

- extractor.py: Main YouTubeChatDownloader class and site entry points.
- video_initialization.py, video_metadata.py, and video_status.py: watch-page
  bootstrap, metadata, and playability state.
- client_context.py and client_requests_*.py: request construction, bootstrap,
  continuation calls, and response-error classification.
- chat_streams.py: Live and replay stream entry points.
- continuation.py: The stateful continuation request loop.
- message_pipeline.py: Action-page parsing, filtering, timing, and diagnostics.
- continuation_helpers.py and continuations.py: Pure loop helpers and the
  response parser.
- parsing/: action routing and message normalization.
"""

from __future__ import annotations

from .extractor import YouTubeChatDownloader

__all__ = ["YouTubeChatDownloader"]
