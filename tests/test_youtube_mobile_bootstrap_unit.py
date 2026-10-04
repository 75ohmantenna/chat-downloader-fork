# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from pathlib import Path

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError

from chat_downloader.errors import ParsingError
from chat_downloader.sites.youtube.video_metadata import YouTubeVideoMetadataCoreMixin
from tests.youtube_third_helpers import http_response

_FIXTURES = Path(__file__).parent / "fixtures/youtube/innertube_bootstrap"
_VIDEO = "IzopCEgh2G8"


def _fixture(name):
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


class _MobileMetadata(YouTubeVideoMetadataCoreMixin):
    def __init__(self, profile="youtube_android", *, authenticated=False):
        self._request_profile = profile
        self._has_auth_cookies = authenticated
        self.calls = []
        self.player = _fixture("youtube-IzopCEgh2G8-player-web.json")
        self.next = _fixture("youtube-IzopCEgh2G8-next-web.json")
        self.error = None
        self.status = 200

    def _session_post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs["json"]))
        if self.error is not None:
            raise self.error
        return http_response(
            status_code=self.status,
            payload=self.player if "/player?" in url else self.next,
        )

    def _session_get(self, url):
        self.calls.append(("GET", url, None))
        player = json.dumps(_fixture("youtube-IzopCEgh2G8-player-web.json"))
        initial = json.dumps(_fixture("youtube-IzopCEgh2G8-next-web.json"))
        html = (
            f"var ytInitialData = {initial};\n"
            f"ytcfg.set({json.dumps({'INNERTUBE_API_KEY': 'fixture'})});\n"
            f"var ytInitialPlayerResponse = {player};\n"
        )
        return http_response(text=html)


@pytest.mark.parametrize("profile", ["youtube_android", "youtube_ios"])
def test_mobile_bootstrap_starts_with_profiled_player_and_next(profile, caplog):
    site = _MobileMetadata(profile)

    details, _, _, config = site._parse_video_data(_VIDEO)

    assert details["original_video_id"] == _VIDEO
    assert [method for method, _, _ in site.calls] == ["POST", "POST"]
    expected = "ANDROID" if profile == "youtube_android" else "IOS"
    assert all(
        call[2]["context"]["client"]["clientName"] == expected for call in site.calls
    )
    assert (
        config["_chat_downloader_bootstrap_diagnostics"]["bootstrap_request_count"] == 2
    )
    assert (
        config["_chat_downloader_bootstrap_diagnostics"]["bootstrap_fallback_count"]
        == 0
    )
    assert not caplog.records


@pytest.mark.parametrize(
    "failure", ["network", "json", "player", "next", "http", "missing-id", "empty-next"]
)
def test_mobile_bootstrap_recovers_through_watch_page(failure, caplog):
    site = _MobileMetadata()
    if failure == "network":
        site.error = RequestsConnectionError("PRIVATE_SENTINEL")
    elif failure == "json":
        site.error = ValueError("PRIVATE_SENTINEL")
    elif failure == "player":
        site.player = {"error": {"message": "PRIVATE_SENTINEL"}}
    elif failure == "next":
        site.next = {"error": {"message": "PRIVATE_SENTINEL"}}
    elif failure == "http":
        site.status = 503
    elif failure == "missing-id":
        site.player["videoDetails"].pop("videoId")
    else:
        site.next = []

    details, _, _, config = site._parse_video_data(_VIDEO)

    assert details["original_video_id"] == _VIDEO
    assert site.calls[-1][0] == "GET"
    diagnostics = config["_chat_downloader_bootstrap_diagnostics"]
    assert diagnostics["bootstrap_fallback_count"] == 1
    assert diagnostics["bootstrap_request_count"] == (
        2 if site.error or failure == "http" else 3
    )
    assert diagnostics["bootstrap_http_error_count"] == (1 if failure == "http" else 0)
    assert "mobile InnerTube bootstrap failed" in caplog.text
    assert "PRIVATE_SENTINEL" not in caplog.text


@pytest.mark.parametrize(
    ("profile", "authenticated", "video_type"),
    [
        ("youtube_web", False, "video"),
        ("youtube_android", True, "video"),
        ("youtube_ios", False, "clip"),
    ],
)
def test_web_authenticated_and_clip_requests_keep_page_bootstrap(
    profile, authenticated, video_type
):
    site = _MobileMetadata(profile, authenticated=authenticated)
    site._parse_video_data(_VIDEO, video_type=video_type)
    assert [call[0] for call in site.calls] == ["GET"]


def test_mobile_bootstrap_rejects_a_mismatched_video():
    site = _MobileMetadata()
    with pytest.raises(ParsingError, match="wrong video"):
        site._parse_video_data("wrong-id")
    assert [call[0] for call in site.calls] == ["POST", "POST"]
