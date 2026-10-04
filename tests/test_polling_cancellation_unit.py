# SPDX-License-Identifier: MIT

from __future__ import annotations

import threading

import pytest

from chat_downloader.utils.timed_generator import TimedGenerator, polling_sleep


@pytest.mark.parametrize("option", ["timeout", "inactivity_timeout"])
def test_deadline_cancels_provider_wait_and_finalizes_accounting(option):
    entered, closed = threading.Event(), threading.Event()
    calls = []

    def provider():
        try:
            entered.set()
            try:
                polling_sleep(60)
            except Exception:
                calls.append("ordinary-error-handler")
            calls.append("post-wait-request")
            yield "late"
        finally:
            closed.set()

    source = TimedGenerator(provider(), **{option: 0.1})
    try:
        assert entered.wait(1)
        assert list(source) == []
        assert closed.is_set()
        assert not source._worker.is_alive()
        assert source.deadline_prefetch_summary() == (0, True)
        assert calls == []
    finally:
        source.close()


def test_cancellation_is_isolated_between_timed_workers():
    entered = [threading.Event(), threading.Event()]
    closed = [threading.Event(), threading.Event()]

    def provider(index):
        try:
            entered[index].set()
            polling_sleep(60 if index == 0 else 0.1)
            yield index
        finally:
            closed[index].set()

    first = TimedGenerator(provider(0))
    second = TimedGenerator(provider(1))
    try:
        assert all(event.wait(1) for event in entered)
        first.close()
        assert closed[0].is_set()
        assert list(second) == [1]
        assert closed[1].is_set()
        polling_sleep(0.001)
    finally:
        first.close()
        second.close()


def test_youtube_poll_wait_cancellation_prevents_another_request(monkeypatch):
    from unittest.mock import Mock

    from chat_downloader.models import ChatRequest
    from chat_downloader.sites.youtube import continuation
    from tests.youtube_third_helpers import Downloader, http_response, response

    entered = threading.Event()
    original_sleep = polling_sleep

    def sleep(seconds):
        entered.set()
        original_sleep(seconds)

    post = Mock(
        return_value=http_response(
            payload=response(
                continuations=[
                    {
                        "timedContinuationData": {
                            "continuation": "next",
                            "timeoutMs": 8000,
                        }
                    }
                ]
            )
        )
    )
    downloader = Downloader()
    downloader._session_post = post
    monkeypatch.setattr(continuation, "_generate_headers", lambda *_: {})
    monkeypatch.setattr(continuation, "_generate_sapisidhash_header", lambda *_: None)
    monkeypatch.setattr(continuation, "polling_sleep", sleep)
    source = TimedGenerator(
        continuation._ContinuationLoop(
            downloader,
            {"status": "live", "continuation_info": {"Live chat": "initial"}},
            {"INNERTUBE_API_KEY": "fixture"},
            ChatRequest(message_groups=["all"]),
        ).run(),
        timeout=0.1,
    )
    try:
        assert entered.wait(1)
        assert list(source) == []
        assert post.call_count == 1
        assert source.deadline_prefetch_summary() == (0, True)
    finally:
        source.close()
