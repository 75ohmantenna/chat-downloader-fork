# SPDX-License-Identifier: MIT

"""Exercise challenged watch-page recovery through bootstrap and replay polling."""

from __future__ import annotations

import json
from contextlib import closing
from copy import deepcopy
from pathlib import Path

from chat_downloader.errors import CaptchaChallengeRequired
from chat_downloader.models import ChatRequest
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from tests.youtube_third_helpers import http_response

_FIXTURES = Path(__file__).parent / "fixtures" / "youtube"


def _fixture(path):
    return json.loads((_FIXTURES / path).read_text())


def test_generic_web_failure_with_tokens_bootstraps_mobile_before_poll(monkeypatch):
    calls = []
    with closing(YouTubeChatDownloader(request_profile="youtube_web")) as site:

        def get(_url, **_kwargs):
            raise CaptchaChallengeRequired("injected watch challenge")

        def post(url, **kwargs):
            payload = deepcopy(kwargs["json"])
            calls.append((url, payload))
            client = payload["context"]["client"]["clientName"]
            if "/player?" in url:
                player = _fixture(
                    "innertube_bootstrap/replay-player-web-unplayable.json"
                )
                if client == "ANDROID":
                    player["playabilityStatus"] = {"status": "OK"}
                return http_response(payload=player)
            if "/next?" in url:
                return http_response(
                    payload=_fixture("innertube_bootstrap/replay-next-mobile.json")
                )
            assert client == "ANDROID", "stale Web token reached polling"
            assert payload["continuation"] == "fixture-mobile-live"
            assert payload["currentPlayerState"]["playerOffsetMs"] == 10000
            return http_response(
                payload=_fixture("live_events/replay-filter-end-boundary.json")
            )

        monkeypatch.setattr(site, "_session_get", get)
        monkeypatch.setattr(site, "_session_post", post)
        chat = site.get_chat_by_video_id(
            "ch8nhMihz04", ChatRequest(start_time=15, end_time=60)
        )
        assert list(chat) == []
        assert site._request_profile == "youtube_android"
    assert len(calls) == 5
