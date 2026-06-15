# SPDX-License-Identifier: MIT

"""Opt-in live Kick checks.

These require outbound network access and a public *live* channel. They are
skipped by default; run with ``--run-network``. They are deliberately lenient:
a Cloudflare/challenge block or an offline channel is reported as a skip (not a
failure), so VPN endpoint reputation or stream state never breaks the suite.
"""

from __future__ import annotations

import os

import pytest

from chat_downloader.errors import CaptchaChallengeRequired, UserNotFound
from chat_downloader.sites.kick.errors import KickError
from chat_downloader.sites.kick.extractor import KickChatDownloader

pytestmark = pytest.mark.network

# Override with KICK_TEST_CHANNEL to point at a channel that is live right now.
_CHANNEL = os.environ.get("KICK_TEST_CHANNEL", "xqc")


def test_live_channel_streams_or_reports_clearly() -> None:
    downloader = KickChatDownloader()
    url = f"https://kick.com/{_CHANNEL}"
    try:
        chat = downloader.get_chat_by_channel(
            _CHANNEL,
            {"url": url, "max_attempts": 1, "max_messages": 3, "timeout": 20},
        )
        received = list(chat.chat)
    except CaptchaChallengeRequired as error:
        pytest.skip(
            "Kick returned a Cloudflare/challenge block — likely VPN/proxy "
            f"endpoint reputation or rate limiting. Try a fresh endpoint. ({error})"
        )
    except KickError as error:
        # Offline channel or incomplete metadata: not a code failure.
        pytest.skip(f"Kick channel unavailable for live test: {error}")
    except UserNotFound:
        pytest.skip(f"Kick channel {_CHANNEL!r} not found; set KICK_TEST_CHANNEL.")
    else:
        for message in received:
            assert message.get("message_type") == "text_message"
            assert message.get("message_id")
    finally:
        downloader.close()
