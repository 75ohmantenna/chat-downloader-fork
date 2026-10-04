# SPDX-License-Identifier: MIT

"""Exercise challenged watch-page recovery through bootstrap and replay polling."""

from __future__ import annotations

import json
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

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
        assert chat.diagnostics["bootstrap_request_count"] == 6
        assert chat.diagnostics["bootstrap_fallback_count"] == 2
        assert chat.diagnostics["bootstrap_profile_switch_count"] == 1
        assert chat.diagnostics["initial_request_profile"] == "youtube_web"
        assert chat.diagnostics["active_request_profile"] == "youtube_android"
        assert chat.diagnostics["chat_view"] == "Live chat"
    assert len(calls) == 5


def test_watch_retry_and_challenge_are_in_bootstrap_http_ledger(monkeypatch):
    with closing(YouTubeChatDownloader(request_profile="youtube_web")) as site:
        get = Mock(
            side_effect=[
                http_response(status_code=500, text="server error"),
                http_response(status_code=429, text="captcha challenge required"),
            ]
        )

        def post(url, **_kwargs):
            if "/player?" in url:
                player = _fixture(
                    "innertube_bootstrap/replay-player-web-unplayable.json"
                )
                player["playabilityStatus"] = {"status": "OK"}
                return http_response(payload=player)
            if "/next?" in url:
                return http_response(
                    payload=_fixture("innertube_bootstrap/replay-next-mobile.json")
                )
            return http_response(
                payload=_fixture("live_events/replay-filter-end-boundary.json")
            )

        monkeypatch.setattr(site, "_session_get", get)
        monkeypatch.setattr(site, "_session_post", post)
        chat = site.get_chat_by_video_id(
            "ch8nhMihz04",
            ChatRequest(start_time=15, end_time=60, max_attempts=2, retry_timeout=0),
        )
        assert list(chat) == []
        assert chat.diagnostics["bootstrap_request_count"] == 4
        assert chat.diagnostics["bootstrap_http_error_count"] == 2
        assert chat.diagnostics["bootstrap_fallback_count"] == 1
        assert chat.diagnostics["continuation_request_count"] == 1
