# SPDX-License-Identifier: MIT

"""DownloaderConfig dataclass — session-level options."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from dataclasses import fields as dc_fields
from typing import Any

from chat_downloader._timeout_defaults import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
)
from chat_downloader.models._base import _cli


@dataclass(slots=True)
class DownloaderConfig:
    """Session-level configuration for :class:`~chat_downloader.ChatDownloader`.

    Maps 1:1 to :meth:`ChatDownloader.__init__` parameters.  This is
    **stable public surface**; add new session-scoped options here.
    """

    # headers is handled specially in the CLI (--user-agent / --header flags)
    # so it has no "cli" metadata here.
    headers: dict[str, str] | None = None

    cookies: str | None = field(
        default=None,
        metadata={
            "cli": _cli(
                "Path to Netscape-format cookies file",
                group="init",
                flags=["-c"],
            ),
        },
    )
    proxy: str | None = field(
        default=None,
        metadata={
            "cli": _cli(
                "Proxy URL (HTTP/HTTPS/SOCKS), e.g. socks5://127.0.0.1:1080."
                " Defaults to None (system proxy settings)",
                group="init",
                flags=["-p"],
            ),
        },
    )
    connect_timeout: float = field(
        default=DEFAULT_CONNECT_TIMEOUT,
        metadata={"cli": _cli("TCP connect timeout in seconds", group="init")},
    )
    read_timeout: float = field(
        default=DEFAULT_READ_TIMEOUT,
        metadata={"cli": _cli("HTTP read timeout in seconds", group="init")},
    )
    request_profile: str | None = field(
        default=None,
        metadata={
            "cli": _cli(
                "Preset request profile "
                "(youtube_web/youtube_android/youtube_ios/twitch_web)",
                group="init",
            ),
        },
    )
    auto_profile_fallback: bool = field(
        default=True,
        metadata={
            "cli": _cli(
                "Auto-switch request profile on repeated incomplete "
                "YouTube continuation responses",
                group="init",
            ),
        },
    )
    twitch_client_id: str | None = field(
        default=None,
        metadata={
            "cli": _cli(
                "Override the public Twitch Client-ID used for GraphQL and "
                "VOD comment requests",
                group="init",
            ),
        },
    )

    def __post_init__(self) -> None:
        """Validate timeout fields."""
        for name, value in (
            ("connect_timeout", self.connect_timeout),
            ("read_timeout", self.read_timeout),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(
                    f"{name} must be a finite positive number, got {value!r}"
                )

    def as_dict(self) -> dict[str, Any]:
        """Return all fields as a plain ``dict``.

        The returned dict is used for session creation
        (``site_class(**config.as_dict())``) and as the supported replacement
        for the removed ``ChatDownloader.init_params`` attribute.
        """
        return {f.name: getattr(self, f.name) for f in dc_fields(self)}


#: All field names belonging to :class:`DownloaderConfig`.
INIT_PARAM_NAMES: frozenset[str] = frozenset(
    f.name for f in dc_fields(DownloaderConfig)
)
