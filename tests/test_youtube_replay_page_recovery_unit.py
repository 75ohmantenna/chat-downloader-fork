# SPDX-License-Identifier: MIT

"""Exact replay-page recovery through real filtering, outputs, and checkpoints."""

from __future__ import annotations

import json
from contextlib import closing
from copy import deepcopy
from pathlib import Path

import pytest

from chat_downloader.errors import NoContinuation
from chat_downloader.formatting import ItemFormatter
from chat_downloader.models import ChatRequest
from chat_downloader.output.capture_parity import audit_capture
from chat_downloader.runtime.chat_pipeline import configure_chat
from chat_downloader.runtime.runner import execute_run
from chat_downloader.sites.models import Chat
from chat_downloader.sites.youtube.continuation import _ContinuationLoop
from chat_downloader.sites.youtube.delivery_recovery import ReplayPageHistory
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from tests.youtube_third_helpers import http_response, response, wrap

FIXTURE = (
    Path(__file__).parent
    / "fixtures/youtube/live_events/replay-filter-end-boundary.json"
)


def _action(message_id="old"):
    action = json.loads(FIXTURE.read_text())["continuationContents"][
        "liveChatContinuation"
    ]["actions"][0]
    replay = action["replayChatItemAction"]
    replay["videoOffsetTimeMsec"] = "1000"
    inner = replay["actions"][0]
    inner["clickTrackingParams"] = "fixture-tracking"
    renderer = inner["addChatItemAction"]["item"]["liveChatTextMessageRenderer"]
    renderer["id"] = message_id
    renderer["message"]["runs"][0]["text"] = message_id
    return action


def _page(actions, token=None):
    continuations = (
        [wrap("timedContinuationData", {"continuation": token, "timeoutMs": 500})]
        if token is not None
        else None
    )
    return response(actions, continuations=continuations)


def _loop(provider, request=None):
    return _ContinuationLoop(
        provider,
        {"status": "was_live", "continuation_info": {"Live chat": "first"}},
        {"INNERTUBE_API_KEY": "fixture"},
        request or ChatRequest(message_groups=["messages"]),
    )


@pytest.fixture(autouse=True)
def _disable_waits(monkeypatch):
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.polling_sleep", lambda _: None
    )


def test_repeated_replay_pages_keep_real_outputs_limits_and_checkpoint_counts(
    tmp_path,
):
    first = _page([_action(), _action()], "second")
    pages = [
        first,
        deepcopy(first),
        _page([_action("new")], "second"),
        _page([_action("end")]),
    ]
    before = deepcopy(pages)
    requests = []

    class ReplayDownloader:
        def __init__(self, **_kwargs):
            self.provider = YouTubeChatDownloader()
            self.responses = iter(deepcopy(pages))

            def post(*_args, **kwargs):
                requests.append(kwargs["json"]["continuation"])
                return http_response(payload=next(self.responses))

            self.provider._session_post = post

        def get_chat(self, **kwargs):
            request = ChatRequest.from_kwargs(**kwargs).resolved_for_site(self.provider)
            loop = _loop(self.provider, request)
            chat = Chat(
                loop.run(),
                id="fixture",
                status="was_live",
                diagnostics=loop.diagnostics,
            )
            configure_chat(chat, request, self.provider)
            return chat

        def close(self):
            self.provider.close()

    raw, txt, checkpoint = (
        tmp_path / "capture.jsonl",
        tmp_path / "capture.txt",
        tmp_path / "checkpoint.json",
    )
    params = {
        "url": "https://www.youtube.com/watch?v=fixture",
        "output": [str(raw), str(txt)],
        "resume": str(checkpoint),
        "message_groups": ["messages"],
        "quiet": True,
        "verify_output": True,
    }
    first_run = execute_run(ReplayDownloader, **params, max_messages=3)
    assert first_run.success
    assert first_run.message_count == 3
    assert first_run.termination_reason == "message_limit"
    assert first_run.provider_diagnostics["emitted_message_count"] == 3
    assert first_run.provider_diagnostics["processed_action_count"] == 3
    assert first_run.provider_diagnostics["replay_repeated_page_count"] == 1
    assert first_run.provider_diagnostics["continuation_request_count"] == 3
    assert json.loads(checkpoint.read_text())["total"] == 3

    resumed = execute_run(ReplayDownloader, **params)
    assert resumed.success
    assert resumed.message_count == 1
    assert resumed.parity_status == "passed"
    assert resumed.provider_diagnostics["checkpoint_overlap_suppressed"] == 3
    assert resumed.provider_diagnostics["replay_repeated_page_count"] == 1
    rows = [json.loads(line) for line in raw.read_text().splitlines()]
    assert [row["message_id"] for row in rows] == ["old", "old", "new", "end"]
    assert len(txt.read_text().splitlines()) == 4
    state = json.loads(checkpoint.read_text())
    assert state["total"] == 4
    assert sum(state["events"].values()) == 4
    assert state["completed"]
    assert requests == [
        "first",
        "second",
        "second",
        "first",
        "second",
        "second",
        "second",
    ]
    assert pages == before


def test_repeated_stale_page_is_suppressed_before_bounded_no_progress_error():
    page = _page([_action()], "first")
    before = deepcopy(page)
    with closing(YouTubeChatDownloader()) as provider:
        provider._session_post = lambda *_a, **_k: http_response(payload=page)
        loop = _loop(provider)
        loop.progress.max_no_progress_polls = 2
        messages = []
        with pytest.raises(NoContinuation, match="No progress"):
            messages.extend(loop.run())
    assert [item["message_id"] for item in messages] == ["old"]
    assert loop.diagnostics["poll_count"] == 3
    assert loop.diagnostics["continuation_request_count"] == 3
    assert loop.diagnostics["processed_action_count"] == 1
    assert loop.diagnostics["emitted_message_count"] == 1
    assert loop.diagnostics["replay_repeated_page_count"] == 2
    assert page == before


def test_two_token_replay_cycle_bounds_silent_polls_and_preserves_outputs(tmp_path):
    pages = {
        "first": _page([_action("a")], "second"),
        "second": _page([_action("b")], "first"),
    }
    before = deepcopy(pages)
    requests = []
    raw, txt = tmp_path / "cycle.jsonl", tmp_path / "cycle.txt"
    request = ChatRequest(
        message_groups=["messages"],
        max_messages=3,
        output=[str(raw), str(txt)],
        format="youtube",
    )

    def post(*_args, **kwargs):
        token = kwargs["json"]["continuation"]
        requests.append(token)
        if len(requests) > 4:
            pytest.fail("Suppressed token cycle did not exhaust the progress bound")
        return http_response(payload=deepcopy(pages[token]))

    with closing(YouTubeChatDownloader()) as provider:
        provider._session_post = post
        loop = _loop(provider, request)
        loop.progress.max_no_progress_polls = 2
        chat = Chat(loop.run(), status="was_live", diagnostics=loop.diagnostics)
        configure_chat(chat, request, provider)
        messages = []
        with pytest.raises(NoContinuation, match="No progress"):
            messages.extend(chat)

    assert requests == ["first", "second", "first", "second"]
    assert [item["message_id"] for item in messages] == ["a", "b"]
    rows = [json.loads(line) for line in raw.read_text().splitlines()]
    assert [item["message_id"] for item in rows] == ["a", "b"]
    assert len(txt.read_text().splitlines()) == 2
    parity = audit_capture(raw, txt, formatter=ItemFormatter(), format_name="youtube")
    assert not parity.failed
    assert loop.diagnostics["poll_count"] == 4
    assert loop.diagnostics["continuation_request_count"] == 4
    assert loop.diagnostics["processed_action_count"] == 2
    assert loop.diagnostics["emitted_message_count"] == 2
    assert loop.diagnostics["replay_repeated_page_count"] == 2
    assert loop.progress.no_progress_count == 2
    assert "termination_reason" not in loop.diagnostics
    assert pages == before


def test_terminal_page_is_distinct_from_same_actions_with_a_continuation():
    pages = iter([_page([_action()], "second"), _page([_action()])])
    with closing(YouTubeChatDownloader()) as provider:
        provider._session_post = lambda *_a, **_k: http_response(payload=next(pages))
        loop = _loop(provider, ChatRequest(message_groups=["all"]))
        messages = list(loop.run())
    assert [item["message_type"] for item in messages] == [
        "text_message",
        "text_message",
        "chat_ended",
    ]
    assert "replay_repeated_page_count" not in loop.diagnostics


def test_page_history_bounds_token_aliases_and_preserves_changed_pages():
    history = ReplayPageHistory(limit=2)
    page = {"actions": [_action()], "continuations": [{"token": "second"}]}
    assert not history.repeated("first", "second", page)
    assert history.repeated("second", "second", deepcopy(page))
    changed = deepcopy(page)
    changed["actions"] = [_action("new")]
    assert not history.repeated("second", "second", changed)
    assert not history.repeated("second", None, {"actions": [_action()]})
    assert len(history.pages) == 2
    assert not history.repeated("first", "second", page)
