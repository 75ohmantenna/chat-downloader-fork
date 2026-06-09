# SPDX-License-Identifier: MIT

"""Typed configuration and request objects for chat-downloader.

Replaces the parameter-bag-via-locals() pattern in
``ChatDownloader.__init__`` and ``ChatDownloader.get_chat()``.

Public surface
--------------
- :class:`DownloaderConfig` — session-level options (headers/cookies/proxy).
- :class:`ChatRequest`       — a single chat retrieval request.

Both dataclasses expose an ``as_dict()`` bridge so that existing internal
helpers that still expect plain dicts continue to work unchanged during the
transition.

CLI metadata
------------
Fields tagged with ``field(metadata={"cli": {...}})`` are auto-wired into the
argparse CLI by :mod:`chat_downloader.cli`.  Fields without a ``"cli"`` key
are internal-only and not exposed as CLI arguments.

CLI metadata keys:

- ``help``   (str, required) — argparse ``help=`` text.
- ``group``  (str, default ``"general"``) — argument group name; must match
  a group registered in :func:`chat_downloader.cli.main`.
- ``flags``  (list[str], optional) — additional short flags, e.g. ``["-s"]``.
"""

from chat_downloader._shared_defaults import DEFAULT_MAX_SEEN_MESSAGE_IDS
from chat_downloader._timeout_defaults import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
)
from chat_downloader.models._base import (
    DEFAULT_BUFFER_SIZE,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MESSAGE_RECEIVE_TIMEOUT,
    get_field_default,
)
from chat_downloader.models._config import INIT_PARAM_NAMES, DownloaderConfig
from chat_downloader.models._request import CHAT_PARAM_NAMES, ChatRequest
from chat_downloader.models._runconfig import (
    RUN_PARAM_NAMES,
    RunConfig,
    coerce_chat_request,
)

__all__ = [
    "CHAT_PARAM_NAMES",
    "DEFAULT_BUFFER_SIZE",
    "DEFAULT_CONNECT_TIMEOUT",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_SEEN_MESSAGE_IDS",
    "DEFAULT_MESSAGE_RECEIVE_TIMEOUT",
    "DEFAULT_READ_TIMEOUT",
    "INIT_PARAM_NAMES",
    "RUN_PARAM_NAMES",
    "ChatRequest",
    "DownloaderConfig",
    "RunConfig",
    "coerce_chat_request",
    "get_field_default",
]
