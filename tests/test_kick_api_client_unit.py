# SPDX-License-Identifier: MIT

from __future__ import annotations

from unittest.mock import patch

import pytest

from chat_downloader.errors import CaptchaChallengeRequired, UserNotFound
from chat_downloader.sites.kick import api_client
from chat_downloader.sites.kick.errors import KickError, KickServerError
from tests.kick_helpers import (
    FakeKickSession,
    FakeResponse,
    load_fixture,
    load_text_fixture,
)


def test_fetch_channel_success() -> None:
    payload = load_fixture("channel_live.json")
    session = FakeKickSession([FakeResponse(200, payload)])
    with patch.object(api_client, "_get_kick_session", return_value=session):
        data = api_client.fetch_channel("examplechannel")
    assert data["id"] == 12345
    assert session.requested_urls == ["https://kick.com/api/v2/channels/examplechannel"]


def test_fetch_channel_closes_internally_owned_session() -> None:
    payload = load_fixture("channel_live.json")

    class CloseableSession(FakeKickSession):
        def __init__(self) -> None:
            super().__init__([FakeResponse(200, payload)])
            self.closed = False

        def close(self) -> None:
            self.closed = True

    session = CloseableSession()
    with patch.object(api_client, "_get_kick_session", return_value=session):
        api_client.fetch_channel("examplechannel")

    assert session.closed is True


def test_close_kick_session_logs_known_close_error(caplog) -> None:
    class BrokenSession:
        @staticmethod
        def close() -> None:
            raise OSError("close failed")

    caplog.set_level("DEBUG", logger=api_client.logger.name)
    api_client._close_kick_session(BrokenSession())

    assert "close failed" in caplog.text


def test_fetch_channel_not_found() -> None:
    session = FakeKickSession([FakeResponse(404, {"message": "not found"})])
    with (
        patch.object(api_client, "_get_kick_session", return_value=session),
        pytest.raises(UserNotFound),
    ):
        api_client.fetch_channel("ghost")


def test_fetch_channel_forbidden_is_challenge() -> None:
    session = FakeKickSession([FakeResponse(403, None, text="denied")])
    with (
        patch.object(api_client, "_get_kick_session", return_value=session),
        pytest.raises(CaptchaChallengeRequired),
    ):
        api_client.fetch_channel("blocked")


def test_fetch_channel_cloudflare_html_is_challenge() -> None:
    html = load_text_fixture("cloudflare_challenge.html")
    session = FakeKickSession(
        [FakeResponse(200, malformed=True, text=html, content_type="text/html")]
    )
    with (
        patch.object(api_client, "_get_kick_session", return_value=session),
        pytest.raises(CaptchaChallengeRequired),
    ):
        api_client.fetch_channel("blocked")


def test_fetch_channel_html_without_markers_is_challenge() -> None:
    session = FakeKickSession(
        [
            FakeResponse(
                200,
                malformed=True,
                text="<html><body>nope</body></html>",
                content_type="text/html",
            )
        ]
    )
    with (
        patch.object(api_client, "_get_kick_session", return_value=session),
        pytest.raises(CaptchaChallengeRequired),
    ):
        api_client.fetch_channel("blocked")


def test_fetch_channel_malformed_json_is_transient() -> None:
    session = FakeKickSession(
        [
            FakeResponse(
                200, malformed=True, text="garbage", content_type="application/json"
            )
        ]
    )
    with (
        patch.object(api_client, "_get_kick_session", return_value=session),
        pytest.raises(KickServerError),
    ):
        api_client.fetch_channel("x")


@pytest.mark.parametrize("status", [429, 500, 503])
def test_fetch_channel_transient_status(status: int) -> None:
    session = FakeKickSession([FakeResponse(status, {"err": True})])
    with (
        patch.object(api_client, "_get_kick_session", return_value=session),
        pytest.raises(KickServerError),
    ):
        api_client.fetch_channel("x")


def test_fetch_channel_unexpected_status() -> None:
    session = FakeKickSession([FakeResponse(418, {"err": True})])
    with (
        patch.object(api_client, "_get_kick_session", return_value=session),
        pytest.raises(KickError),
    ):
        api_client.fetch_channel("x")


def test_fetch_channel_non_object_payload() -> None:
    session = FakeKickSession([FakeResponse(200, ["not", "an", "object"])])
    with (
        patch.object(api_client, "_get_kick_session", return_value=session),
        pytest.raises(KickServerError),
    ):
        api_client.fetch_channel("x")


def test_fetch_preloaded_messages_success() -> None:
    payload = load_fixture("preloaded_messages.json")
    session = FakeKickSession([FakeResponse(200, payload)])
    with patch.object(api_client, "_get_kick_session", return_value=session):
        messages = api_client.fetch_preloaded_messages("12345", "examplechannel")
    assert [m["id"] for m in messages] == ["preloaded-2", "preloaded-1"]
    assert session.requested_urls == ["https://kick.com/api/v2/channels/12345/messages"]


def test_fetch_preloaded_messages_transient_is_empty() -> None:
    session = FakeKickSession([FakeResponse(500, {"err": True})])
    with patch.object(api_client, "_get_kick_session", return_value=session):
        assert api_client.fetch_preloaded_messages("1", "x") == []


def test_fetch_preloaded_messages_non_dict_is_empty() -> None:
    session = FakeKickSession([FakeResponse(200, ["not", "a", "dict"])])
    with patch.object(api_client, "_get_kick_session", return_value=session):
        assert api_client.fetch_preloaded_messages("1", "x") == []


def test_fetch_preloaded_messages_missing_messages_is_empty() -> None:
    session = FakeKickSession([FakeResponse(200, {"data": {}})])
    with patch.object(api_client, "_get_kick_session", return_value=session):
        assert api_client.fetch_preloaded_messages("1", "x") == []


def test_fetch_preloaded_messages_filters_non_dict_entries() -> None:
    payload = {"data": {"messages": [{"id": "a"}, "bad", 7]}}
    session = FakeKickSession([FakeResponse(200, payload)])
    with patch.object(api_client, "_get_kick_session", return_value=session):
        messages = api_client.fetch_preloaded_messages("1", "x")
    assert messages == [{"id": "a"}]
