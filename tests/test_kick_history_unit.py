# SPDX-License-Identifier: MIT

"""Recorded dense history and adversarial reverse-recovery contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Event
from unittest.mock import Mock

import pytest

from chat_downloader.sites.kick import history
from chat_downloader.sites.kick.api_client import KickApiClient
from chat_downloader.sites.kick.errors import KickError, KickServerError
from tests.kick_helpers import (
    FakeKickSession,
    FakeResponse,
    load_fixture,
    message_page,
    request,
)

START = datetime(2026, 9, 11, 16, 7, 2, tzinfo=UTC)


def message(identifier, offset=0):
    return {
        "id": identifier,
        "created_at": (START + timedelta(seconds=offset)).isoformat(),
    }


def collect(client, *, seconds=9, **kwargs):
    return list(
        history.iter_reverse_history(
            client,
            "12345",
            START,
            START + timedelta(seconds=seconds),
            request(),
            **kwargs,
        )
    )


def test_recorded_capped_pages_recover_every_dense_message():
    fixture = load_fixture("history_dense_recorded.json")
    start, end = map(datetime.fromisoformat, fixture["provenance"]["capture_interval"])
    session = FakeKickSession(
        [FakeResponse(200, page) for page in fixture["reverse_pages"]]
    )
    actual = list(
        history.iter_reverse_history(
            KickApiClient(session=session), "12345", start, end, request()
        )
    )
    expected = {
        row["id"]
        for page in fixture["reverse_pages"]
        for row in page["data"]["messages"]
        if start <= datetime.fromisoformat(row["created_at"]) <= end
    }
    capped_ids = {
        row["id"]
        for page in fixture["capped_forward_pages"]
        for row in page["data"]["messages"]
    }
    assert expected - capped_ids
    assert {row["id"] for row in actual} == expected
    assert len(actual) == len(expected)
    assert len(session.calls) == 4
    assert [call[1]["params"] for call in session.calls] == [
        {"cursor": history._cursor_after(end)},
        *({"cursor": page["data"]["cursor"]} for page in fixture["reverse_pages"][:-1]),
    ]


def test_empty_pages_with_continuation_and_same_second_overlap():
    client = Mock()
    client.fetch_message_page.side_effect = [
        message_page([], cursor="a"),
        message_page([message("same-1", 2), message("same-2", 2)], cursor="b"),
        message_page([message("same-2", 2), message("same-3", 2)], cursor=None),
    ]
    assert {row["id"] for row in collect(client)} == {"same-1", "same-2", "same-3"}
    assert client.fetch_message_page.call_count == 3


def test_reverse_boundary_filters_and_keeps_the_whole_last_page():
    client = Mock()
    client.fetch_message_page.return_value = message_page(
        [
            "bad",
            {},
            {"created_at": 1},
            {"created_at": "bad"},
            message("before", -1),
            message("inside", 1),
            message("after", 20),
            {"created_at": START.isoformat()},
            {"id": None, "created_at": START.isoformat()},
        ],
        cursor="unused",
    )
    rows = collect(client)
    assert [row.get("id") for row in rows] == [None, None, "inside"]
    client.fetch_message_page.assert_called_once()


@pytest.mark.parametrize("seconds", [-1])
def test_empty_bounds_do_not_request(seconds):
    client = Mock()
    assert collect(client, seconds=seconds) == []
    client.fetch_message_page.assert_not_called()


def test_history_retries_and_stops_at_exhaustion():
    client = Mock()
    client.fetch_message_page.side_effect = [
        OSError("transient"),
        message_page([message("ok")]),
    ]
    assert collect(client) == [message("ok")]
    assert client.fetch_message_page.call_count == 2


def test_equal_inclusive_bounds_recover_the_boundary_second():
    client = Mock()
    client.fetch_message_page.return_value = message_page([message("boundary")])
    assert collect(client, seconds=0) == [message("boundary")]


@pytest.mark.parametrize("kwargs", [{"max_pages": 0}, {"max_records": 0}])
def test_empty_budget_is_explicitly_incomplete(kwargs):
    client = Mock()
    with pytest.raises(KickError, match="bounded limit"):
        collect(client, **kwargs)
    client.fetch_message_page.assert_not_called()


@pytest.mark.parametrize("kwargs", [{"max_pages": 1}, {"max_records": 1}])
def test_budget_preserves_collected_prefix_then_reports_incomplete(kwargs):
    client = Mock()
    client.fetch_message_page.return_value = message_page(
        [message("ok")], cursor="next"
    )
    source = history.iter_reverse_history(
        client, "12345", START, START + timedelta(seconds=9), request(), **kwargs
    )
    assert next(source) == message("ok")
    with pytest.raises(KickError, match="bounded limit"):
        next(source)


def test_oversized_last_page_cannot_certify_exhaustion():
    client = Mock()
    client.fetch_message_page.return_value = message_page(
        [message("one"), message("two")]
    )
    source = history.iter_reverse_history(
        client, "12345", START, START + timedelta(seconds=9), request(), max_records=1
    )
    assert next(source)["id"] == "one"
    with pytest.raises(KickError, match="raw-record limit"):
        next(source)


@pytest.mark.parametrize(
    ("pages", "match"),
    [
        (
            [
                message_page([], cursor="a"),
                message_page([], cursor="b"),
                message_page([], cursor="a"),
            ],
            "cursor repeated",
        ),
        (
            [
                message_page(
                    [], cursor=history._cursor_after(START + timedelta(seconds=9))
                )
            ],
            "move backwards",
        ),
        (
            [
                message_page([message("same")], cursor="a"),
                message_page([message("same")], cursor="b"),
            ],
            "duplicate page",
        ),
        ([message_page([], cursor="9" * 5000)], "move backwards"),
    ],
)
def test_broken_pagination_is_explicitly_incomplete(pages, match):
    client = Mock()
    client.fetch_message_page.side_effect = pages
    with pytest.raises(KickError, match=match):
        collect(client)


def test_numeric_cursor_progress_and_naive_provider_time():
    client = Mock()
    client.fetch_message_page.side_effect = [
        message_page([], cursor="1"),
        message_page(
            [{"id": "naive", "created_at": START.replace(tzinfo=None).isoformat()}]
        ),
    ]
    assert collect(client)[0]["id"] == "naive"


@pytest.mark.parametrize("during_fetch", [False, True])
def test_cancellation_stops_requests_and_does_not_publish_late_results(during_fetch):
    cancelled = Event()
    client = Mock()

    def fetch(*args, **kwargs):
        cancelled.set()
        return message_page([message("late")])

    client.fetch_message_page.side_effect = fetch
    if not during_fetch:
        cancelled.set()
    assert collect(client, cancelled=cancelled) == []
    assert client.fetch_message_page.call_count == int(during_fetch)


@pytest.mark.parametrize(
    "data", [{}, {"messages": {}}, {"messages": [], "cursor": 123}]
)
def test_history_validates_page_contract(data):
    client = Mock()
    client.fetch_message_page.return_value = {"data": data}
    with pytest.raises(KickServerError):
        history.fetch_validated_page(client, "1", cursor="reverse")


def test_forward_validation_remains_available_to_api_callers():
    client = Mock()
    client.fetch_message_page.return_value = message_page([])
    assert history.fetch_validated_page(client, "1", start_time="date") == ([], None)
    client.fetch_message_page.assert_called_once_with("1", start_time="date")
