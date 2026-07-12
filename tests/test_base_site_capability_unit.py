# SPDX-License-Identifier: MIT

"""Provider-neutral live-format capability defaults on BaseChatDownloader.

The generic runtime asks the site — via ``is_live_status`` and
``resolve_live_format`` — instead of sniffing site names. These tests pin the
base defaults that non-overriding sites (Twitch, Kick) rely on.
"""

from __future__ import annotations

from chat_downloader.sites.base import BaseChatDownloader


class _BareSite(BaseChatDownloader):
    """A site that does not override the live-format capability."""


def test_is_live_status_defaults_to_false() -> None:
    site = _BareSite()

    # Base _LIVE_STATUSES is empty, so nothing counts as live.
    assert site.is_live_status("live") is False
    assert site.is_live_status("past") is False
    assert site.is_live_status(None) is False


def test_resolve_live_format_defaults_to_identity() -> None:
    site = _BareSite()

    assert site.resolve_live_format("default") == "default"
    assert site.resolve_live_format("24_hour") == "24_hour"
