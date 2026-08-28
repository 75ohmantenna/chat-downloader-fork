# SPDX-License-Identifier: MIT

"""Offline coverage for Kick timestamp-forward history pagination."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from chat_downloader.models import ChatRequest
from chat_downloader.sites.kick import history
from chat_downloader.sites.kick.api_client import KickApiClient
from chat_downloader.sites.kick.errors import (
    KickError,
    KickForwardHistoryRejected,
)
from tests.kick_helpers import FakeKickSession, FakeResponse, load_fixture


def _request(*, max_attempts: int = 1) -> ChatRequest:
    return ChatRequest(
        max_attempts=max_attempts,
        retry_timeout=0,
        interruptible_retry=False,
    )


def test_forward_history_composes_real_client_and_orders_observed_pages() -> None:
    pages = load_fixture("message_history_forward_pages.json")
    session = FakeKickSession([FakeResponse(200, page) for page in pages])
    client = KickApiClient(session=session)

    messages = list(
        history.iter_forward_history(
            client,
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
            _request(),
        )
    )

    assert [message["id"] for message in messages] == [
        "forward-1",
        "forward-2",
        "forward-3",
        "forward-4",
    ]
    assert [call[1]["params"] for call in session.calls] == [
        {"start_time": "2026-01-01T00:00:00.000000Z"},
        {"start_time": "2026-01-01T00:00:00.432953Z"},
    ]


def test_format_history_start_normalizes_naive_and_offset_timestamps() -> None:
    naive = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    offset = datetime.fromisoformat("2026-01-01T01:00:00+01:00")

    assert history.format_history_start(naive) == "2026-01-01T00:00:00.000000Z"
    assert history.format_history_start(offset) == "2026-01-01T00:00:00.000000Z"


@pytest.mark.parametrize(
    "cursor",
    ["", "-1", "1.5", chr(0xFF11), "not-a-cursor", "1" * 5_000],
)
def test_start_after_cursor_rejects_invalid_values(cursor: str) -> None:
    assert history._start_after_cursor(cursor) is None


def test_start_after_cursor_rejects_datetime_overflow() -> None:
    assert history._start_after_cursor(str(10**30)) is None


def test_forward_history_skips_unusable_records_and_preserves_timestamp_ties() -> None:
    page = {
        "data": {
            "messages": [
                {"id": "before", "created_at": "2025-12-31T23:59:59Z"},
                "not-an-object",
                {"id": "missing-time"},
                {"id": "number-time", "created_at": 123},
                {"id": "bad-time", "created_at": "invalid"},
                {"id": "tie-1", "created_at": "2026-01-01T00:00:00"},
                {"id": "tie-2", "created_at": "2026-01-01T00:00:00Z"},
            ],
            "cursor": None,
        }
    }
    client = Mock()
    client.fetch_message_page.return_value = page

    messages = list(
        history.iter_forward_history(
            client,
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            _request(),
        )
    )

    assert [message["id"] for message in messages] == ["tie-1", "tie-2"]


def test_forward_history_does_not_fetch_empty_or_reversed_window() -> None:
    client = Mock()

    assert (
        list(
            history.iter_forward_history(
                client,
                "123",
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 1, tzinfo=UTC),
                _request(),
            )
        )
        == []
    )
    client.fetch_message_page.assert_not_called()


def test_forward_history_retries_transient_fetch_failure() -> None:
    client = Mock()
    client.fetch_message_page.side_effect = [
        OSError("temporary"),
        {"data": {"messages": []}},
    ]

    assert (
        list(
            history.iter_forward_history(
                client,
                "123",
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 2, tzinfo=UTC),
                _request(max_attempts=2),
            )
        )
        == []
    )
    assert client.fetch_message_page.call_count == 2


def test_forward_history_propagates_first_rejected_response() -> None:
    client = Mock()
    client.fetch_message_page.side_effect = KickForwardHistoryRejected("no")

    with pytest.raises(KickForwardHistoryRejected):
        list(
            history.iter_forward_history(
                client,
                "123",
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 2, tzinfo=UTC),
                _request(),
            )
        )


def test_forward_history_does_not_restart_after_partial_output() -> None:
    first_page = {
        "data": {
            "messages": [{"id": "first", "created_at": "2026-01-01T00:00:00Z"}],
            "cursor": "1767225600000000",
        }
    }
    client = Mock()
    client.fetch_message_page.side_effect = [
        first_page,
        KickForwardHistoryRejected("changed"),
    ]
    iterator = history.iter_forward_history(
        client,
        "123",
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 2, tzinfo=UTC),
        _request(),
    )

    assert next(iterator)["id"] == "first"
    with pytest.raises(KickError, match="after pagination began"):
        next(iterator)


def test_forward_history_stops_on_duplicate_page_before_reemitting() -> None:
    page = {
        "data": {
            "messages": [{"id": "same", "created_at": "2026-01-01T00:00:00Z"}],
            "cursor": "1767225600000000",
        }
    }
    client = Mock()
    client.fetch_message_page.return_value = page

    messages = list(
        history.iter_forward_history(
            client,
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            _request(),
        )
    )

    assert [message["id"] for message in messages] == ["same"]
    assert client.fetch_message_page.call_count == 2


def test_forward_history_deduplicates_overlap_without_dropping_same_second() -> None:
    pages = [
        {
            "data": {
                "messages": [{"id": "first", "created_at": "2026-01-01T00:00:00Z"}],
                "cursor": "1767225600000000",
            }
        },
        {
            "data": {
                "messages": [
                    {"id": "first", "created_at": "2026-01-01T00:00:01Z"},
                    {
                        "id": "second",
                        "created_at": "2026-01-01T00:00:00Z",
                    },
                ],
                "cursor": None,
            }
        },
    ]
    client = Mock()
    client.fetch_message_page.side_effect = pages

    messages = list(
        history.iter_forward_history(
            client,
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            _request(),
        )
    )

    assert [message["id"] for message in messages] == ["first", "second"]


def test_forward_history_deduplicates_ids_within_one_page() -> None:
    client = Mock()
    client.fetch_message_page.return_value = {
        "data": {
            "messages": [
                {"id": "same", "created_at": "2026-01-01T00:00:00Z"},
                {"id": "same", "created_at": "2026-01-01T00:00:01Z"},
            ],
            "cursor": None,
        }
    }

    messages = list(
        history.iter_forward_history(
            client,
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            _request(),
        )
    )

    assert [message["id"] for message in messages] == ["same"]


def test_forward_history_deduplicates_numeric_ids_within_and_across_pages() -> None:
    pages = [
        {
            "data": {
                "messages": [
                    {"id": 7, "created_at": "2026-01-01T00:00:00Z"},
                    {"id": 7, "created_at": "2026-01-01T00:00:00Z"},
                ],
                "cursor": "1767225600000000",
            }
        },
        {
            "data": {
                "messages": [
                    {"id": 7, "created_at": "2026-01-01T00:00:00Z"},
                    {"id": 8, "created_at": "2026-01-01T00:00:00Z"},
                ],
                "cursor": None,
            }
        },
    ]
    client = Mock()
    client.fetch_message_page.side_effect = pages

    messages = list(
        history.iter_forward_history(
            client,
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            _request(),
        )
    )

    assert [message["id"] for message in messages] == [7, 8]


def test_forward_history_suppresses_cross_page_timestamp_regressions() -> None:
    pages = [
        {
            "data": {
                "messages": [{"id": "first", "created_at": "2026-01-01T00:00:01Z"}],
                "cursor": "1767225601500000",
            }
        },
        {
            "data": {
                "messages": [
                    {"id": "older", "created_at": "2026-01-01T00:00:00Z"},
                    {"id": "equal", "created_at": "2026-01-01T00:00:01Z"},
                ],
                "cursor": None,
            }
        },
    ]
    client = Mock()
    client.fetch_message_page.side_effect = pages

    messages = list(
        history.iter_forward_history(
            client,
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            _request(),
        )
    )

    assert [message["id"] for message in messages] == ["first", "equal"]


def test_forward_history_continues_cursor_past_inclusive_visible_end() -> None:
    pages = [
        {
            "data": {
                "messages": [{"id": "end-1", "created_at": "2026-01-01T00:00:01Z"}],
                "cursor": "1767225601400000",
            }
        },
        {
            "data": {
                "messages": [
                    {"id": "end-2", "created_at": "2026-01-01T00:00:01Z"},
                    {"id": "after", "created_at": "2026-01-01T00:00:02Z"},
                ],
                "cursor": "1767225602000000",
            }
        },
    ]
    client = Mock()
    client.fetch_message_page.side_effect = pages

    messages = list(
        history.iter_forward_history(
            client,
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
            _request(),
        )
    )

    assert [message["id"] for message in messages] == ["end-1", "end-2"]
    assert client.fetch_message_page.call_count == 2


@pytest.mark.parametrize("cursor", [None, "invalid"])
def test_forward_history_stops_on_missing_or_invalid_cursor(
    cursor: object,
) -> None:
    client = Mock()
    client.fetch_message_page.return_value = {
        "data": {
            "messages": [{"id": "one", "created_at": "2026-01-01T00:00:00Z"}],
            "cursor": cursor,
        }
    }

    assert (
        len(
            list(
                history.iter_forward_history(
                    client,
                    "123",
                    datetime(2026, 1, 1, tzinfo=UTC),
                    datetime(2026, 1, 2, tzinfo=UTC),
                    _request(),
                )
            )
        )
        == 1
    )


def test_forward_history_stops_on_repeated_cursor() -> None:
    client = Mock()
    client.fetch_message_page.return_value = {
        "data": {
            "messages": [{"id": "one", "created_at": "2026-01-01T00:00:00Z"}],
            "cursor": "1767225599999999",
        }
    }

    assert list(
        history.iter_forward_history(
            client,
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            _request(),
        )
    ) == [{"id": "one", "created_at": "2026-01-01T00:00:00Z"}]


def test_forward_history_stops_on_novel_regressive_cursor() -> None:
    client = Mock()
    client.fetch_message_page.return_value = {
        "data": {
            "messages": [{"id": "one", "created_at": "2026-01-01T00:00:00Z"}],
            "cursor": "1767225599000000",
        }
    }

    messages = list(
        history.iter_forward_history(
            client,
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            _request(),
        )
    )

    assert [message["id"] for message in messages] == ["one"]
    client.fetch_message_page.assert_called_once()


@pytest.mark.parametrize(
    "page",
    [
        {},
        {"data": []},
        {"data": {}},
        {"data": {"messages": {}}},
        {"data": {"messages": [], "cursor": 123}},
    ],
)
def test_forward_history_retries_malformed_nested_page(
    page: dict[str, object],
) -> None:
    client = Mock()
    client.fetch_message_page.side_effect = [
        page,
        {"data": {"messages": [], "cursor": None}},
    ]

    assert (
        list(
            history.iter_forward_history(
                client,
                "123",
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 2, tzinfo=UTC),
                _request(max_attempts=2),
            )
        )
        == []
    )
    assert client.fetch_message_page.call_count == 2
