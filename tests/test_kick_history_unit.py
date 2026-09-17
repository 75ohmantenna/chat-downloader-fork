# SPDX-License-Identifier: MIT

"""Regression contracts for Kick's five-second forward history windows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from chat_downloader.models import ChatRequest
from chat_downloader.sites.kick import history
from chat_downloader.sites.kick.api_client import KickApiClient
from chat_downloader.sites.kick.errors import (
    KickForwardHistoryRejected,
    KickServerError,
)
from tests.kick_helpers import FakeKickSession, FakeResponse, load_fixture

START = datetime(2026, 9, 11, 16, 7, 2, tzinfo=UTC)


def message(identifier, offset=0):
    return {
        "id": identifier,
        "created_at": (START + timedelta(seconds=offset)).isoformat(),
    }


def page_client(messages):
    client = Mock()
    client.fetch_message_page.return_value = {"data": {"messages": messages}}
    return client


def collect(client, *, seconds=9, **kwargs):
    return list(
        history.iter_forward_history(
            client,
            "12345",
            START,
            START + timedelta(seconds=seconds),
            ChatRequest(max_attempts=2, retry_timeout=0, interruptible_retry=False),
            **kwargs,
        )
    )


def test_sparse_history_ignores_reverse_cursor_and_reads_empty_windows() -> None:
    fixture = load_fixture("history_sparse_windows.json")
    session = FakeKickSession([FakeResponse(200, page) for page in fixture["pages"]])
    result = collect(KickApiClient(session=session))
    assert [item["id"] for item in result] == ["window-message"]
    assert [call[1]["params"] for call in session.calls] == [
        {"start_time": "2026-09-11T16:07:02.000000Z"},
        {"start_time": "2026-09-11T16:07:07.000000Z"},
    ]


def test_history_orders_deduplicates_and_filters_malformed_or_outside_records() -> None:
    client = page_client(
        [
            "bad",
            {},
            {"id": "bad-time", "created_at": 1},
            {"id": "bad-time", "created_at": "bad"},
            message("before", -1),
            message("later", 2),
            message("first"),
            message("after", 20),
        ]
    )
    assert [item["id"] for item in collect(client)] == ["first", "later"]
    assert client.fetch_message_page.call_count == 2


@pytest.mark.parametrize("seconds", [0, -1])
def test_history_empty_bounds_do_not_request(seconds) -> None:
    client = Mock()
    assert collect(client, seconds=seconds) == []
    client.fetch_message_page.assert_not_called()


def test_history_retries_and_preserves_idless_raw_records() -> None:
    raw = {"created_at": START.isoformat()}
    client = Mock()
    client.fetch_message_page.side_effect = [
        OSError("transient"),
        {"data": {"messages": [raw]}},
    ]
    assert collect(client, seconds=1) == [raw]
    assert client.fetch_message_page.call_count == 2


@pytest.mark.parametrize("limit", ["max_pages", "max_records"])
def test_history_bounds_work_before_another_request(limit, caplog) -> None:
    client = page_client([message(str(index)) for index in range(3)])
    result = collect(client, **{limit: 1})
    assert len(result) == (1 if limit == "max_records" else 3)
    assert client.fetch_message_page.call_count == 1
    assert "limit" in caplog.text


def test_history_propagates_start_field_rejection() -> None:
    client = Mock()
    client.fetch_message_page.side_effect = KickForwardHistoryRejected("rejected")
    with pytest.raises(KickForwardHistoryRejected):
        collect(client)


@pytest.mark.parametrize(
    "data", [{}, {"messages": {}}, {"messages": [], "cursor": 123}]
)
def test_history_validates_page_contract(data) -> None:
    client = Mock()
    client.fetch_message_page.return_value = {"data": data}
    with pytest.raises(KickServerError):
        history.fetch_validated_page(client, "1", cursor="reverse")


def test_history_format_normalizes_naive_and_offset_times() -> None:
    assert (
        history.format_history_start(START.replace(tzinfo=None))
        == "2026-09-11T16:07:02.000000Z"
    )
    assert (
        history.format_history_start(
            datetime.fromisoformat("2026-09-11T17:07:02+01:00")
        )
        == "2026-09-11T16:07:02.000000Z"
    )
    assert history._message_timestamp({"created_at": "2026-09-11T16:07:02"}) == START
