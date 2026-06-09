# SPDX-License-Identifier: MIT

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chat_downloader.errors import CaptchaChallengeRequired
from chat_downloader.models import ChatRequest
from chat_downloader.sites.youtube.client_requests_bootstrap import (
    get_innertube_video_bootstrap,
)
from chat_downloader.sites.youtube.video_metadata import (
    YouTubeVideoMetadataCoreMixin,
)

_FIXTURE_DIR = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "youtube"
    / "innertube_bootstrap"
)


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))


class _JsonResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FallbackDummy(YouTubeVideoMetadataCoreMixin):
    def __init__(self) -> None:
        self._request_profile = None
        self.post_urls: list[str] = []

    def _session_get(self, _url: str):
        return SimpleNamespace(
            status_code=429,
            text="<html>captcha challenge required</html>",
            url="https://www.google.com/sorry/index",
        )

    def _session_post(self, url: str, **_kwargs):
        self.post_urls.append(url)
        if "/player?" in url:
            return _JsonResponse(
                _load_fixture("youtube-IzopCEgh2G8-player-web.json")
            )
        if "/next?" in url:
            return _JsonResponse(
                _load_fixture("youtube-IzopCEgh2G8-next-web.json")
            )
        raise AssertionError(f"unexpected URL: {url}")


def test_innertube_bootstrap_extracts_primary_live_continuation() -> None:
    calls: list[tuple[str, dict]] = []

    def session_post(url: str, **kwargs):
        calls.append((url, kwargs["json"]))
        if "/player?" in url:
            return _JsonResponse(
                _load_fixture("youtube-IzopCEgh2G8-player-web.json")
            )
        if "/next?" in url:
            return _JsonResponse(
                _load_fixture("youtube-IzopCEgh2G8-next-web.json")
            )
        raise AssertionError(f"unexpected URL: {url}")

    yt_initial_data, ytcfg, player_response = get_innertube_video_bootstrap(
        "IzopCEgh2G8",
        session_post,
        None,
    )

    continuation_info = yt_initial_data["_chat_downloader_continuation_info"]
    assert continuation_info["Live chat"].startswith("0ofMyAO")
    assert continuation_info["Top chat"].startswith("0ofMyAM")
    assert ytcfg["INNERTUBE_API_KEY"]
    assert ytcfg["INNERTUBE_CONTEXT"]["client"]["clientName"] == "WEB"
    assert ytcfg["INNERTUBE_CONTEXT"]["client"]["visitorData"]
    assert player_response["videoDetails"]["videoId"] == "IzopCEgh2G8"
    assert [call[1]["videoId"] for call in calls] == [
        "IzopCEgh2G8",
        "IzopCEgh2G8",
    ]


def test_parse_video_data_falls_back_after_watch_challenge(monkeypatch) -> None:
    from chat_downloader.sites.youtube import client_requests_initial

    dummy = _FallbackDummy()
    monkeypatch.setattr(
        client_requests_initial,
        "get_title_of_webpage",
        lambda _html: "https://www.youtube.com/watch?v=IzopCEgh2G8",
    )

    details, player_response, yt_initial_data, ytcfg = dummy._parse_video_data(
        "IzopCEgh2G8",
        ChatRequest(url="https://www.youtube.com/watch?v=IzopCEgh2G8"),
    )

    assert details["status"] == "live"
    assert details["title"] == "Destiny 2"
    assert details["continuation_info"]["Live chat"].startswith("0ofMyAO")
    assert player_response["videoDetails"]["isLive"] is True
    assert yt_initial_data["_chat_downloader_continuation_info"]
    assert ytcfg["INNERTUBE_CONTEXT"]["client"]["visitorData"]
    assert len(dummy.post_urls) == 2


def test_parse_video_data_does_not_fallback_for_clips(monkeypatch) -> None:
    from chat_downloader.sites.youtube import client_requests_initial

    dummy = _FallbackDummy()
    monkeypatch.setattr(
        client_requests_initial,
        "get_title_of_webpage",
        lambda _html: "https://www.youtube.com/clip/abc",
    )

    with pytest.raises(CaptchaChallengeRequired):
        dummy._parse_video_data("abc", video_type="clip")

    assert dummy.post_urls == []


def test_build_fallback_ytcfg_handles_non_dict_client() -> None:
    from chat_downloader.sites.youtube.client_requests_bootstrap import (
        _build_fallback_ytcfg,
    )

    result = _build_fallback_ytcfg(
        context={"client": "not-a-dict"},
        player_response={},
        next_response={},
    )
    assert isinstance(result, dict)
