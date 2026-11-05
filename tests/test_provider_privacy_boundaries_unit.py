# SPDX-License-Identifier: MIT

from __future__ import annotations

from contextlib import closing
from json import JSONDecodeError
from time import time
from unittest.mock import Mock

import pytest
import requests
from requests.adapters import BaseAdapter

import chat_downloader.debugging as debugging
from chat_downloader.errors import InvalidParameter, ParsingError
from chat_downloader.models import ChatRequest
from chat_downloader.sites.models import Chat
from chat_downloader.sites.session import ChatDownloaderSession
from chat_downloader.sites.twitch.extractor import TwitchChatDownloader
from chat_downloader.sites.youtube.client_auth import _generate_sapisidhash_header
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from tests.core_third_helpers import captured_logs, restore_loggers


class _RecordingAdapter(BaseAdapter):
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        status, headers, body = next(self.responses)
        response = requests.Response()
        response.status_code = status
        response.headers.update(headers)
        response._content_consumed = True
        response._content = body.encode()
        response.url = request.url
        response.request = request
        return response

    def close(self):
        pass


def _install_recording_transport(http, adapter):
    # Isolate manual redirect/privacy policy from the bounded wire transport.
    # Real-server HTTP-policy tests exercise that separate ownership layer.
    prepared_session = requests.Session()
    prepared_session.headers.update(http.session.headers)
    prepared_session.cookies = http.session.cookies
    prepared_session.trust_env = False
    http.session.close()
    http.session = prepared_session
    prepared_session.mount("https://", adapter)
    prepared_session.mount("http://", adapter)


def _record_http(responses):
    http = ChatDownloaderSession(
        headers={
            "X-Goog-Visitor-Id": "synthetic-visitor",
            "X-Goog-PageId": "synthetic-account",
            "X-Custom-Secret": "synthetic-secret",
            "Referer": "https://www.youtube.com/watch?v=private",
        },
        proxy="",
    )
    adapter = _RecordingAdapter(responses)
    _install_recording_transport(http, adapter)
    return http, adapter


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("status", [300, 301, 302, 303, 307, 308, 399])
def test_provider_api_redirect_is_rejected_before_json(method, status):
    http, adapter = _record_http(
        [(status, {"Location": "https://foreign.invalid/"}, '{"data":{}}')]
    )
    with pytest.raises(ParsingError, match="redirect"):
        getattr(http, method)("https://www.youtube.com/youtubei/v1/browse")
    assert len(adapter.requests) == 1


@pytest.mark.parametrize("method", ["get", "post"])
def test_malformed_redirect_preparation_is_content_free(method):
    http, adapter = _record_http(
        [(302, {"Location": "https://[invalid/PRIVATE_SENTINEL"}, "")]
    )
    with pytest.raises(ParsingError) as error:
        getattr(http, method)("https://www.youtube.com/youtubei/v1/browse")
    assert "PRIVATE_SENTINEL" not in str(error.value)
    assert len(adapter.requests) == 1


@pytest.mark.parametrize(
    "destination",
    [
        "https://foreign.invalid/collect",
        "https://m.youtube.com/collect",
        "http://www.youtube.com/collect",
        "https://www.youtube.com:444/collect",
        "https://user@www.youtube.com/collect",
        "https://[invalid",
    ],
)
def test_initial_page_redirect_never_forwards_private_headers(destination):
    http, adapter = _record_http([(302, {"Location": destination}, "")])
    with pytest.raises(ParsingError, match="redirect"):
        http.get("https://www.youtube.com/watch?v=example", allow_redirects=True)
    assert len(adapter.requests) == 1
    assert adapter.requests[0].headers["X-Custom-Secret"] == "synthetic-secret"


def test_malformed_initial_origin_is_content_free_and_never_requested():
    http, adapter = _record_http([])
    with pytest.raises(ParsingError) as error:
        http.get("https://www.youtube.com:PRIVATE_SENTINEL/watch", allow_redirects=True)
    assert "PRIVATE_SENTINEL" not in str(error.value)
    assert adapter.requests == []


def test_unprepared_malformed_redirect_is_redacted_without_forwarding():
    http, _adapter = _record_http([])
    response = requests.Response()
    response.status_code = 302
    response._content_consumed = True
    response.headers["Location"] = "https://[invalid/PRIVATE_SENTINEL"
    http.session.get = Mock(return_value=response)
    with pytest.raises(ParsingError) as error:
        http.get("https://www.youtube.com/watch", allow_redirects=True)
    assert "PRIVATE_SENTINEL" not in str(error.value)
    assert http.session.get.call_count == 1


def test_initial_page_allows_bounded_same_origin_https_redirects():
    http, adapter = _record_http(
        [
            (302, {"Location": "/watch?v=canonical"}, ""),
            (200, {}, "provider page"),
        ]
    )
    assert (
        http.get("https://www.youtube.com/watch?v=old", allow_redirects=True).text
        == "provider page"
    )
    assert [request.url for request in adapter.requests] == [
        "https://www.youtube.com/watch?v=old",
        "https://www.youtube.com/watch?v=canonical",
    ]


@pytest.mark.parametrize("location", ["/loop", None])
def test_initial_redirect_loops_and_missing_locations_fail_closed(location):
    headers = {} if location is None else {"Location": location}
    http, adapter = _record_http([(302, headers, "")] * 6)
    with pytest.raises(ParsingError, match="redirect"):
        http.get("https://www.youtube.com/watch?v=old", allow_redirects=True)
    assert len(adapter.requests) == (1 if location is None else 6)


@pytest.mark.parametrize(
    "url",
    [
        "https://foreign.invalid/playlist?list=synthetic",
        "https://www.youtube.com.foreign.invalid/playlist?list=synthetic",
        "http://www.youtube.com/playlist?list=synthetic",
        "https://user:synthetic-secret@www.youtube.com/playlist?list=synthetic",
        "https://www.youtube.com:444/playlist?list=synthetic",
        "https://www.youtube.com/watch?list=synthetic",
        "https://www.youtube.com/playlist?list=first&list=second",
        "https://www.youtube.com/playlist?list=",
        "https://www.youtube.com/playlist?list=%2Fother",
        "https://[invalid/playlist?list=synthetic",
    ],
)
def test_public_playlist_rejects_unchecked_destinations_without_request(url):
    downloader = YouTubeChatDownloader()
    get = Mock(side_effect=AssertionError("must not request"))
    downloader._session_get = get
    with pytest.raises(InvalidParameter) as error:
        list(downloader.get_playlist_items(url))
    assert "synthetic-secret" not in str(error.value)
    get.assert_not_called()


@pytest.mark.parametrize("provider", ["youtube", "twitch"])
@pytest.mark.parametrize(
    "bad_scope",
    [
        {"domain": ".foreign.invalid"},
        {"domain": ".youtube.com.foreign.invalid"},
        {"path": "/unrelated"},
        {"expire_time": 1},
        {"port": "444"},
    ],
)
@pytest.mark.parametrize("positive", [False, True])
@pytest.mark.parametrize("wrapper", ["_download_base_gql", "_download_gql"])
def test_auth_consumers_ignore_inapplicable_cookie_metadata(
    provider, bad_scope, positive, wrapper
):
    youtube = provider == "youtube"
    downloader = YouTubeChatDownloader() if youtube else TwitchChatDownloader()
    domain = ".youtube.com" if youtube else ".twitch.tv"
    name = "__Secure-3PAPISID" if youtube else "auth-token"
    options = {"domain": domain, "secure": True, **bad_scope}
    downloader.set_cookie_value(name=name, value="synthetic-rejected", **options)
    if positive:
        downloader.set_cookie_value(domain, name, "synthetic-valid", secure=True)
    if youtube:
        header = _generate_sapisidhash_header(downloader, "https://www.youtube.com")
        assert bool(header) is positive
        promoted = [c for c in downloader.session.cookies if c.name == "SAPISID"]
        assert [c.value for c in promoted] == (["synthetic-valid"] if positive else [])
    else:
        response = Mock(status_code=200, text="", json=lambda: [{"data": {}}])
        post = Mock(return_value=response)
        downloader._session_post = post
        getattr(downloader, wrapper)([{"operationName": "StreamMetadata"}])
        headers = post.call_args.kwargs["headers"]
        assert headers.get("Authorization") == (
            "OAuth synthetic-valid" if positive else None
        )
        assert post.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("reverse", [False, True])
def test_ambiguous_authentication_values_fail_closed_independent_of_jar_order(reverse):
    downloader = YouTubeChatDownloader()
    domains = [".youtube.com", "www.youtube.com"]
    for index, domain in enumerate(reversed(domains) if reverse else domains):
        downloader.set_cookie_value(domain, "SAPISID", str(index), secure=True)
    assert _generate_sapisidhash_header(downloader, "https://www.youtube.com") is None
    # Public cookie introspection remains name-only for compatibility.
    assert downloader.get_cookie_value("SAPISID") is not None


def test_sid_promotion_preserves_host_only_scope_port_and_expiry():
    downloader = YouTubeChatDownloader()
    expiry = round(time()) + 60
    downloader.set_cookie_value(
        "www.youtube.com",
        "__Secure-3PAPISID",
        "synthetic-valid",
        port="443",
        secure=True,
        expire_time=expiry,
    )
    source = next(
        c for c in downloader.session.cookies if c.name == "__Secure-3PAPISID"
    )
    source.domain_specified = False
    assert _generate_sapisidhash_header(downloader, "https://www.youtube.com")
    promoted = next(c for c in downloader.session.cookies if c.name == "SAPISID")
    assert promoted.domain == source.domain
    assert promoted.domain_specified is False
    assert promoted.path == source.path
    assert promoted.port == source.port
    assert promoted.expires == expiry
    assert source.name == "__Secure-3PAPISID"


@pytest.mark.parametrize("host", ["youtube.com", "www.youtube.com", "m.youtube.com"])
def test_public_playlist_canonicalizes_valid_provider_before_request(host):
    downloader = YouTubeChatDownloader()
    html = (
        '<script id="yt-initial-data">{"contents":{"empty":true}}</script>'
        '<script>ytcfg.set({"INNERTUBE_API_KEY":"synthetic"});</script>'
    )
    reply = Mock(status_code=200, text=html, url="https://www.youtube.com/playlist")
    get = Mock(return_value=reply)
    downloader._session_get = get
    assert (
        list(
            downloader.get_playlist_items(
                f"https://{host}:443/playlist?list=PL_synthetic-1&ignored=private#fragment"
            )
        )
        == []
    )
    get.assert_called_once_with(
        "https://www.youtube.com/playlist?list=PL_synthetic-1", allow_redirects=True
    )


@pytest.mark.parametrize("wrapper", ["_download_base_gql", "_download_gql"])
@pytest.mark.parametrize("reverse", [False, True])
def test_twitch_ambiguous_auth_values_fail_closed(wrapper, reverse):
    downloader = TwitchChatDownloader()
    domains = [".twitch.tv", "www.twitch.tv"]
    for index, domain in enumerate(reversed(domains) if reverse else domains):
        downloader.set_cookie_value(domain, "auth-token", str(index), secure=True)
    response = Mock(status_code=200, text="", json=lambda: [{"data": {}}])
    post = Mock(return_value=response)
    downloader._session_post = post
    getattr(downloader, wrapper)([{"operationName": "StreamMetadata"}])
    assert "Authorization" not in post.call_args.kwargs["headers"]


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("route", ["@example/live", "channel/UC_example/live"])
def test_live_user_shortcut_uses_https_before_sending_auth(monkeypatch, scheme, route):
    downloader = YouTubeChatDownloader(proxy="")
    downloader.update_session_headers({"Authorization": "Bearer SYNTHETIC_AUTH"})
    html = (
        '<script id="yt-initial-data">{"contents":{"empty":true}}</script>'
        "<script>var ytInitialPlayerResponse = "
        '{"videoDetails":{"videoId":"LLpNUqHVam8"}};</script>'
    )
    adapter = _RecordingAdapter([(200, {}, html)])
    _install_recording_transport(downloader._http, adapter)
    monkeypatch.setattr(
        downloader, "get_chat_by_video_id", lambda *_args: Chat(iter(()))
    )
    url = f"{scheme}://www.youtube.com/{route}"
    match_info = downloader.matches(url)
    assert match_info is not None
    with closing(
        downloader._get_chat_by_live_user(match_info[1], ChatRequest(url=url))
    ):
        pass
    assert [request.url for request in adapter.requests] == [
        f"https://www.youtube.com/{route}"
    ]
    assert adapter.requests[0].headers["Authorization"] == "Bearer SYNTHETIC_AUTH"
    downloader.close()


def test_twitch_decoder_retry_does_not_disclose_response_title():
    downloader = TwitchChatDownloader(proxy="")
    body = '{"message":"<title>PRIVATE_CHAT_SENTINEL</title>"'
    adapter = _RecordingAdapter([(200, {}, body)])
    _install_recording_transport(downloader._http, adapter)
    with restore_loggers(), captured_logs(safe=True) as stream:
        debugging.set_log_level("debug")
        with pytest.raises(JSONDecodeError) as error:
            downloader._download_base_gql([])
        downloader.retry(
            1,
            max_attempts=2,
            retry_timeout=0,
            interruptible_retry=False,
            error=error.value,
        )
        assert "PRIVATE_CHAT_SENTINEL" not in stream.getvalue()
    assert len(adapter.requests) == 1
    downloader.close()
