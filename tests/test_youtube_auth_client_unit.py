# SPDX-License-Identifier: MIT

from __future__ import annotations

import hashlib
from json.decoder import JSONDecodeError

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError

import chat_downloader.sites.youtube.client_auth as _yt_auth
import chat_downloader.sites.youtube.client_context as _yt_context
import chat_downloader.sites.youtube.client_requests_continuation as _yt_continuation  # noqa: E501
import chat_downloader.sites.youtube.client_requests_initial as _yt_initial
from chat_downloader.errors import VideoNotFound

# Convenience aliases that mirror the old ``client`` facade names.
_parse_data_sync_id = _yt_auth._parse_data_sync_id
_make_sid_authorization = _yt_auth._make_sid_authorization
_generate_sapisidhash_header = _yt_auth._generate_sapisidhash_header
_initialize_consent = _yt_auth._initialize_consent
_get_sid_cookies = _yt_auth._get_sid_cookies
_extract_account_syncid = _yt_context._extract_account_syncid
_generate_headers = _yt_context._generate_headers
_get_innertube_context = _yt_context._get_innertube_context
_get_continuation_info = _yt_continuation._get_continuation_info
_get_initial_info = _yt_initial._get_initial_info


class _FakeSession:
    def __init__(self, cookies=None) -> None:
        self.cookies = dict(cookies or {})
        self.set_calls = []

    def get_cookie_value(self, name, default=None):
        return self.cookies.get(name, default)

    def set_cookie_value(self, domain, name, value, **kwargs) -> None:
        self.set_calls.append((domain, name, value, kwargs))
        self.cookies[name] = value


class _Resp:
    def __init__(self, status_code, payload=None, text="") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self._json_calls = 0

    def json(self):
        self._json_calls += 1
        payload = self._payload
        if callable(payload):
            return payload(self._json_calls)
        return payload


class _PageResp:
    def __init__(self, status_code, text) -> None:
        self.status_code = status_code
        self.text = text


_SUCCESS_CONTINUATION_PAYLOAD = {
    "continuationContents": {"liveChatContinuation": {"actions": []}},
}


def test_parse_data_sync_id_handles_empty_single_and_delegated_values() -> None:
    assert _parse_data_sync_id(None) == (None, None)
    assert _parse_data_sync_id("") == (None, None)
    assert _parse_data_sync_id("user-session") == (None, "user-session")
    assert _parse_data_sync_id("delegated||user") == (
        "delegated",
        "user",
    )


def test_make_sid_authorization_supports_additional_parts() -> None:
    auth = _make_sid_authorization(
        "SAPISIDHASH",
        "sid-value",
        "https://www.youtube.com",
        123,
        {"session_id": "user-session"},
    )

    expected_hash = hashlib.sha1(
        b"user-session 123 sid-value https://www.youtube.com",
    ).hexdigest()
    assert auth == f"SAPISIDHASH 123_{expected_hash}_user-session"


def test_generate_sapisidhash_header_returns_none_without_sid_cookies() -> None:
    session = _FakeSession()

    assert (
        _generate_sapisidhash_header(session, "https://www.youtube.com") is None
    )
    assert session.set_calls == []


def test_generate_sapisidhash_header_promotes_cookie_and_uses_datasync_id(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.client_auth.time.time",
        lambda: 1234,
    )
    session = _FakeSession(
        {
            "__Secure-1PAPISID": "onep",
            "__Secure-3PAPISID": "threep",
        },
    )

    header = _generate_sapisidhash_header(
        session,
        "https://www.youtube.com",
        {"DATASYNC_ID": "delegated||user-session"},
    )

    assert session.set_calls == [
        (
            ".youtube.com",
            "SAPISID",
            "threep",
            {"secure": True, "expire_time": 4834},
        ),
    ]
    assert header == " ".join(
        [
            _make_sid_authorization(
                "SAPISIDHASH",
                "threep",
                "https://www.youtube.com",
                1234,
                {"session_id": "user-session"},
            ),
            _make_sid_authorization(
                "SAPISID1PHASH",
                "onep",
                "https://www.youtube.com",
                1234,
                {"session_id": "user-session"},
            ),
            _make_sid_authorization(
                "SAPISID3PHASH",
                "threep",
                "https://www.youtube.com",
                1234,
                {"session_id": "user-session"},
            ),
        ],
    )


def test_generate_sapisidhash_header_uses_sapisid_when_already_present(
    monkeypatch,
) -> None:
    """SAPISID cookie present: _ensure_primary_sapisid returns it directly."""
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.client_auth.time.time",
        lambda: 1234,
    )
    session = _FakeSession({"SAPISID": "direct-sapisid"})

    header = _generate_sapisidhash_header(
        session, "https://www.youtube.com", ytcfg=None
    )

    # No promotion call should have been made — cookie already exists.
    assert session.set_calls == []
    assert header is not None
    assert "SAPISIDHASH" in header


def test_generate_sapisidhash_header_no_session_id_when_ytcfg_none(
    monkeypatch,
) -> None:
    """ytcfg=None: _session_id_parts returns None (early return, line 117)."""
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.client_auth.time.time",
        lambda: 1234,
    )
    session = _FakeSession({"__Secure-3PAPISID": "threep"})

    header = _generate_sapisidhash_header(
        session, "https://www.youtube.com", ytcfg=None
    )

    assert header is not None


def test_generate_sapisidhash_header_no_session_id_when_datasync_id_absent(
    monkeypatch,
) -> None:
    """Ytcfg present but no DATASYNC_ID: _session_id_parts returns None."""
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.client_auth.time.time",
        lambda: 1234,
    )
    session = _FakeSession({"__Secure-3PAPISID": "threep"})

    header = _generate_sapisidhash_header(
        session, "https://www.youtube.com", ytcfg={"OTHER_KEY": "value"}
    )

    assert header is not None


def test_initialize_consent_returns_early_for_existing_secure_cookie() -> None:
    session = _FakeSession({"__Secure-3PSID": "present"})

    _initialize_consent(session)

    assert session.set_calls == []


def test_initialize_consent_skips_non_consented_socs_cookie() -> None:
    session = _FakeSession({"SOCS": "XYZ"})

    _initialize_consent(session)

    assert session.set_calls == []


def test_initialize_consent_sets_cookie_for_missing_or_consented_socs() -> None:
    missing = _FakeSession()
    consented = _FakeSession({"SOCS": "CAAabc"})

    _initialize_consent(missing)
    _initialize_consent(consented)

    assert missing.set_calls == [
        (".youtube.com", "SOCS", "CAI", {"secure": True})
    ]
    assert consented.set_calls == [
        (".youtube.com", "SOCS", "CAI", {"secure": True})
    ]


def test_get_sid_cookies_returns_all_variants() -> None:
    session = _FakeSession(
        {
            "SAPISID": "sap",
            "__Secure-1PAPISID": "onep",
            "__Secure-3PAPISID": "threep",
        },
    )

    assert _get_sid_cookies(session) == ("sap", "onep", "threep")


def test_extract_account_syncid_prefers_delegated_datasync_and_falls_back() -> (
    None
):
    assert _extract_account_syncid({"DATASYNC_ID": "delegated||user"}) == (
        "delegated"
    )
    assert _extract_account_syncid({"DATASYNC_ID": "user-only"}) is None
    assert _extract_account_syncid({"DELEGATED_SESSION_ID": "fallback"}) == (
        "fallback"
    )


def test_generate_headers_handles_optional_auth_and_minimal_paths() -> None:
    ytcfg = {
        "INNERTUBE_CONTEXT_CLIENT_NAME": 1,
        "INNERTUBE_CLIENT_VERSION": "2.0",
        "ID_TOKEN": "id-token",
        "DATASYNC_ID": "delegated||user",
        "SESSION_INDEX": 5,
        "LOGGED_IN": True,
        "INNERTUBE_CONTEXT": {
            "client": {
                "visitorData": "visitor-1",
                "userAgent": "TestAgent/1.0",
            },
        },
    }

    headers = _generate_headers(
        ytcfg,
        session=object(),
        yt_home="https://www.youtube.com",
        sapisidhash_generator=lambda *_a, **_k: "AUTH",
    )
    minimal_headers = _generate_headers(
        {
            "INNERTUBE_CONTEXT_CLIENT_NAME": 1,
            "INNERTUBE_CLIENT_VERSION": "2.0",
        },
        session=object(),
        yt_home="https://www.youtube.com",
        sapisidhash_generator=lambda *_a, **_k: None,
    )

    assert headers["x-youtube-identity-token"] == "id-token"
    assert headers["x-goog-pageid"] == "delegated"
    assert headers["x-goog-authuser"] == "5"
    assert headers["x-goog-visitor-id"] == "visitor-1"
    assert headers["user-agent"] == "TestAgent/1.0"
    assert headers["x-youtube-bootstrap-logged-in"] == "true"
    assert headers["authorization"] == "AUTH"
    assert minimal_headers["x-goog-authuser"] == "0"
    assert "authorization" not in minimal_headers
    assert "x-goog-pageid" not in minimal_headers


def test_get_innertube_context_handles_non_dict_and_missing_client() -> None:
    assert _get_innertube_context({"INNERTUBE_CONTEXT": "invalid"}) == {}

    empty_context = _get_innertube_context({"INNERTUBE_CONTEXT": []})

    context = _get_innertube_context(
        {
            "INNERTUBE_CONTEXT": {
                "client": None,
            },
        },
    )

    assert empty_context == {
        "client": {
            "hl": "en",
            "timeZone": "UTC",
            "utcOffsetMinutes": 0,
        },
    }
    assert context == {
        "client": {
            "hl": "en",
            "timeZone": "UTC",
            "utcOffsetMinutes": 0,
        },
    }


def test_get_continuation_info_logs_non_retriable_http_errors_without_json_body() -> (  # noqa: E501
    None
):
    response = _Resp(
        404,
        payload=lambda call_number: (
            (_ for _ in ()).throw(ValueError("bad json"))
            if call_number == 1
            else {}
        ),
    )

    result = _get_continuation_info(
        "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
        lambda *_a, **_k: response,
        {"max_attempts": 1},
        json={"continuation": "abc"},
    )

    assert result == {}
    assert response._json_calls == 2


def test_get_continuation_info_retries_after_json_decode_error() -> None:
    calls = {"count": 0}

    def session_post(_url, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return _Resp(
                200,
                payload=lambda _n: (_ for _ in ()).throw(
                    JSONDecodeError("bad json", "doc", 0),
                ),
                text="not-json",
            )
        return _Resp(200, _SUCCESS_CONTINUATION_PAYLOAD)

    result = _get_continuation_info(
        "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
        session_post,
        {"max_attempts": 2},
        json={"continuation": "abc"},
    )

    assert result == _SUCCESS_CONTINUATION_PAYLOAD
    assert calls["count"] == 2


def test_get_continuation_info_raises_retries_exceeded_on_json_parse_failure() -> (  # noqa: E501
    None
):
    """Exhausted retries on JSONDecodeError now surface as RetriesExceeded."""
    from chat_downloader.errors import RetriesExceeded

    with pytest.raises(RetriesExceeded, match="Unable to parse JSON"):
        _get_continuation_info(
            "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
            lambda *_a, **_k: _Resp(
                200,
                payload=lambda _n: (_ for _ in ()).throw(
                    JSONDecodeError("bad json", "doc", 0),
                ),
                text="not-json",
            ),
            {"max_attempts": 1},
            json={"continuation": "abc"},
        )


def test_get_continuation_info_raises_retries_exceeded_on_network_error() -> (
    None
):
    """Exhausted retries on a network error now surface as RetriesExceeded."""
    from chat_downloader.errors import RetriesExceeded

    with pytest.raises(RetriesExceeded, match="ConnectionError"):
        _get_continuation_info(
            "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
            lambda *_a, **_k: (_ for _ in ()).throw(
                RequestsConnectionError("boom")
            ),
            {"max_attempts": 1},
            json={"continuation": "abc"},
        )


def test_get_continuation_info_raises_when_attempts_disabled() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        _get_continuation_info(
            "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
            lambda *_a, **_k: _Resp(200, _SUCCESS_CONTINUATION_PAYLOAD),
            {"max_attempts": 0},
            json={"continuation": "abc"},
        )


def test_get_initial_info_raises_video_not_found(monkeypatch) -> None:
    monkeypatch.setattr(_yt_initial, "regex_search", lambda *_a, **_k: "{}")
    monkeypatch.setattr(
        _yt_initial,
        "try_parse_json",
        lambda _value, default=None: (
            {"contents": {}} if default is None else default
        ),
    )
    monkeypatch.setattr(
        _yt_initial, "get_title_of_webpage", lambda _html: "Missing"
    )

    with pytest.raises(VideoNotFound, match="Missing"):
        _get_initial_info(
            "https://www.youtube.com/watch?v=missing",
            lambda _url: _PageResp(404, "<html>missing</html>"),
            {"max_attempts": 1},
            r"ytInitialData",
            r"ytcfg",
            r"ytInitialPlayerResponse",
        )


def test_get_initial_info_raises_network_error_without_retry() -> None:
    with pytest.raises(RequestsConnectionError):
        _get_initial_info(
            "https://www.youtube.com/watch?v=test",
            lambda _url: (_ for _ in ()).throw(RequestsConnectionError("boom")),
            {"max_attempts": 1},
            r"ytInitialData",
            r"ytcfg",
            r"ytInitialPlayerResponse",
        )


def test_get_initial_info_retries_network_error_before_success(
    monkeypatch,
) -> None:
    calls = {"count": 0}

    def session_get(_url):
        calls["count"] += 1
        if calls["count"] == 1:
            msg = "boom"
            raise RequestsConnectionError(msg)
        return _PageResp(200, "<html>ok</html>")

    monkeypatch.setattr(_yt_initial, "regex_search", lambda *_a, **_k: "{}")
    monkeypatch.setattr(
        _yt_initial,
        "try_parse_json",
        lambda _value, default=None: (
            {"contents": {}} if default is None else default
        ),
    )

    yt_initial_data, ytcfg, player_response = _get_initial_info(
        "https://www.youtube.com/watch?v=test",
        session_get,
        {"max_attempts": 2},
        r"ytInitialData",
        r"ytcfg",
        r"ytInitialPlayerResponse",
    )

    assert calls["count"] == 2
    assert yt_initial_data == {"contents": {}}
    assert ytcfg == {}
    assert player_response == {}


def test_get_initial_info_raises_retries_exceeded_when_attempts_disabled() -> (
    None
):
    with pytest.raises(ValueError, match="max_attempts"):
        _get_initial_info(
            "https://www.youtube.com/watch?v=test",
            lambda _url: _PageResp(200, "<html>ok</html>"),
            {"max_attempts": 0},
            r"ytInitialData",
            r"ytcfg",
            r"ytInitialPlayerResponse",
        )


def test_get_initial_info_uses_default_attempts_when_params_missing(
    monkeypatch,
) -> None:
    monkeypatch.setattr(_yt_initial, "regex_search", lambda *_a, **_k: "{}")
    monkeypatch.setattr(
        _yt_initial,
        "try_parse_json",
        lambda _value, default=None: (
            {"contents": {}} if default is None else default
        ),
    )

    yt_initial_data, ytcfg, player_response = _get_initial_info(
        "https://www.youtube.com/watch?v=test",
        lambda _url: _PageResp(200, "<html>ok</html>"),
        None,
        r"ytInitialData",
        r"ytcfg",
        r"ytInitialPlayerResponse",
    )

    assert yt_initial_data == {"contents": {}}
    assert ytcfg == {}
    assert player_response == {}


def test_initialize_pref_merges_existing_cookie() -> None:
    from unittest.mock import MagicMock

    from chat_downloader.sites.youtube.client_auth import _initialize_pref

    mock_session = MagicMock()
    mock_session.get_cookie_value.return_value = "f1=val1&f2=val2"

    _initialize_pref(mock_session)

    call_args = mock_session.set_cookie_value.call_args
    value = call_args[0][2]  # Third positional arg is the cookie value
    assert "hl=en" in value
    assert "tz=UTC" in value
    assert "f1=val1" in value  # Original values preserved


def test_initialize_pref_ignores_unparseable_existing_cookie(
    monkeypatch,
) -> None:
    from unittest.mock import MagicMock

    from chat_downloader.sites.youtube import client_auth

    mock_session = MagicMock()
    mock_session.get_cookie_value.return_value = "broken"
    monkeypatch.setattr(
        client_auth,
        "parse_qsl",
        MagicMock(side_effect=ValueError),
    )

    client_auth._initialize_pref(mock_session)

    mock_session.set_cookie_value.assert_called_once_with(
        ".youtube.com", "PREF", "hl=en&tz=UTC"
    )
