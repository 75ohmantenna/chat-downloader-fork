# SPDX-License-Identifier: MIT

"""Canonical typed configuration, request, and run models.

Public surface
--------------
- :class:`DownloaderConfig` — session-level options (headers/cookies/proxy).
- :class:`ChatRequest`       — a single chat retrieval request.
- :class:`RunConfig`         — CLI/runtime presentation and control options.
- :class:`SiteDefault`       — marker for site-specific default values.

The configuration dataclasses expose ``as_dict()`` bridges for internal
boundaries that still consume mappings.

CLI metadata
------------
Fields tagged with ``field(metadata={"cli": {...}})`` provide defaults, help
text, and short flags used by :mod:`chat_downloader.cli_args`; parser
registration remains explicit there. ``DownloaderConfig.headers`` is exposed
through the separately registered ``--user-agent`` and ``--header`` options.

CLI metadata keys:

- ``help``   (str, required) — argparse ``help=`` text.
- ``group``  (str, default ``"general"``) — declarative argument-group owner;
  registration in :mod:`chat_downloader.cli_args` must use the same group.
- ``flags``  (list[str], optional) — additional short flags, e.g. ``["-s"]``.
"""

from __future__ import annotations

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
from chat_downloader.models._site_default import SiteDefault

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
    "SiteDefault",
    "coerce_chat_request",
    "get_field_default",
]
