# SPDX-License-Identifier: MIT

"""Same-video channel reacquisition without stream-wide raw deduplication."""

from __future__ import annotations

import json
from contextlib import closing
from copy import deepcopy
from pathlib import Path

import pytest

from chat_downloader.formatting import ItemFormatter
from chat_downloader.models import ChatRequest
from chat_downloader.output.capture_parity import audit_capture
from chat_downloader.runtime.chat_pipeline import configure_chat
from chat_downloader.runtime.runner import execute_run
from chat_downloader.sites.models import Chat
from chat_downloader.sites.youtube.delivery_recovery import (
    ChannelDeliveryHistory,
    ChildDiagnostics,
)
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from tests.youtube_third_helpers import http_response, response, wrap


def _action(message_id, text=None, *, replacement=False):
    fixture = (
        Path(__file__).parent
        / "fixtures/youtube/live_events/replay-filter-end-boundary.json"
    )
    action = json.loads(fixture.read_text())["continuationContents"][
        "liveChatContinuation"
    ]["actions"][0]["replayChatItemAction"]["actions"][0]
    item = action["addChatItemAction"]["item"]
    renderer = item["liveChatTextMessageRenderer"]
    renderer["id"] = message_id
    renderer["message"]["runs"][0]["text"] = text or message_id
    if replacement:
        return wrap("replaceChatItemAction.replacementItem", item)
    return action


def _page(actions, token):
    return response(
        actions,
        continuations=[
            wrap("timedContinuationData", {"continuation": token, "timeoutMs": 500})
        ],
    )


def test_two_pass_same_video_failure_backlog_keeps_outputs_and_accounting(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.polling_sleep", lambda _: None
    )
    monkeypatch.setattr(
        "chat_downloader.utils.timed_generator.polling_sleep", lambda _: None
    )
    discovered, bootstrapped, requested = [], [], []
    first = _page([_action("a"), _action("a"), _action("b")], "broken")
    second = _page(
        [
            _action("a"),
            _action("a"),
            _action("b"),
            _action("a", replacement=True),
            _action("a", "replacement revision", replacement=True),
            _action("c"),
            _action("a"),
        ],
        "unused",
    )

    class ChannelDownloader:
        def __init__(self, **_kwargs):
            self.provider = YouTubeChatDownloader()

            def discover(**kwargs):
                discovered.append(kwargs)
                return iter(
                    [
                        {
                            "video_id": "same-video",
                            "title": "fixture",
                            "video_type": "LIVE",
                        }
                    ]
                )

            def bootstrap(video_id, _request):
                bootstrapped.append(video_id)
                token = f"child-{len(bootstrapped)}"
                return {
                    "status": "live",
                    "continuation_info": {"Live chat": token},
                }, {
                    "INNERTUBE_API_KEY": "fixture",
                    "_chat_downloader_bootstrap_diagnostics": {
                        "bootstrap_request_count": 2,
                        "initial_request_profile": "youtube_web",
                    },
                }

            def post(*_args, **kwargs):
                continuation = kwargs["json"]["continuation"]
                requested.append(continuation)
                if continuation == "broken":
                    return http_response(
                        status_code=400,
                        payload={"error": {"code": 400, "message": "fixture failure"}},
                    )
                return http_response(
                    payload=deepcopy(first if continuation == "child-1" else second)
                )

            self.provider.get_user_videos = discover
            self.provider._get_initial_video_info = bootstrap
            self.provider._session_post = post

        def get_chat(self, **kwargs):
            request = ChatRequest.from_kwargs(**kwargs)
            chat = self.provider.get_chat_by_channel_id("fixture", request)
            configure_chat(chat, request, self.provider)
            return chat

        def close(self):
            self.provider.close()

    raw, txt = tmp_path / "capture.jsonl", tmp_path / "capture.txt"
    result = execute_run(
        ChannelDownloader,
        url="https://www.youtube.com/channel/fixture",
        output=[str(raw), str(txt)],
        max_messages=7,
        message_groups=["messages"],
        format="youtube_live_default",
        quiet=True,
    )
    assert result.success
    assert result.message_count == 7
    assert result.termination_reason == "message_limit"
    assert bootstrapped == ["same-video", "same-video"]
    assert len(discovered) == 2
    assert requested == ["child-1", "broken", "child-2"]
    rows = [json.loads(line) for line in raw.read_text().splitlines()]
    assert [row["message_id"] for row in rows] == ["a", "a", "b", "a", "a", "c", "a"]
    assert [row["action_type"] for row in rows] == [
        "add_chat_item",
        "add_chat_item",
        "add_chat_item",
        "replace_chat_item",
        "replace_chat_item",
        "add_chat_item",
        "add_chat_item",
    ]
    assert rows[4]["message"] == "replacement revision"
    assert len(txt.read_text().splitlines()) == 7
    parity = audit_capture(
        raw, txt, formatter=ItemFormatter(), format_name="youtube_live_default"
    )
    assert not parity.failed
    diagnostics = result.provider_diagnostics
    assert diagnostics["channel_overlap_suppressed_count"] == 3
    assert diagnostics["poll_count"] == 2
    assert diagnostics["processed_action_count"] == 10
    assert diagnostics["emitted_message_count"] == 10
    assert diagnostics["continuation_request_count"] == 3
    assert diagnostics["http_error_count"] == 1
    assert diagnostics["bootstrap_request_count"] == 4


def _item(**changes):
    return {
        "message_id": "same-id",
        "message_type": "paid_message",
        "action_type": "add_chat_item",
        "message": "original",
        **changes,
    }


def test_overlap_counts_preserve_event_kinds_payloads_and_bound_history():
    deliveries = ChannelDeliveryHistory(max_videos=1, max_events=3)
    initial = deliveries.begin("a")
    paid = _item(received_timestamp=1, time_in_seconds=-10, time_text="-0:10")
    ticker = _item(message_type="ticker_paid_message_item")
    assert initial.accept(paid)
    assert initial.accept(paid)
    assert initial.accept(ticker)
    overlap = deliveries.begin("a")
    assert overlap.accept({"message": "unidentified welcome"})
    later = {
        **paid,
        "received_timestamp": 2,
        "time_in_seconds": -2,
        "time_text": "-0:02",
    }
    assert not overlap.accept(later)
    assert not overlap.accept(later)
    assert not overlap.accept(ticker)
    assert overlap.accept(paid)  # The old occurrence budget is exhausted.
    assert overlap.accept(_item(action_type="replace_chat_item"))
    assert overlap.accept(_item(action_type="replace_chat_item", message="revision"))
    resumed = deliveries.begin("a")
    assert resumed.accept(_item(message="new payload"))  # Ends bootstrap overlap.
    assert resumed.accept(_item(action_type="replace_chat_item"))
    assert len(deliveries.videos["a"].events) == 3
    assert sum(deliveries.videos["a"].counts.values()) == 3
    assert deliveries.begin("b").accept(paid)  # IDs never cross video boundaries.
    assert deliveries.begin("a").accept(paid)  # The least-recent video was evicted.
    assert list(deliveries.videos) == ["a"]


def test_overlap_recognizes_identified_target_actions_without_a_message_id():
    deliveries = ChannelDeliveryHistory()
    deletion = {"target_message_id": "deleted", "action_type": "remove_chat_item"}
    assert deliveries.begin("video").accept(deletion)
    assert not deliveries.begin("video").accept(deletion)


def test_child_diagnostics_accumulate_without_aliasing_or_double_counting():
    target = {
        "poll_count": 2,
        "parse_error": 1,
        "non_emission_counts": {"filtered": 2},
        "initial_request_profile": "youtube_web",
    }
    source = {
        "poll_count": 1,
        "parse_error": 2,
        "non_emission_counts": {"filtered": 1, "invalid": "not a count"},
        "initial_request_profile": "youtube_android",
        "active_request_profile": "youtube_android",
        "provider_marker": 42,
    }
    child = ChildDiagnostics(target)
    child.update(source)
    child.update(source)
    assert target["poll_count"] == 3
    assert target["parse_error"] == 3
    assert target["non_emission_counts"] == {"filtered": 3}
    assert target["initial_request_profile"] == "youtube_web"
    assert target["provider_marker"] == 42
    source["non_emission_counts"]["filtered"] = 2
    child.update(source)
    assert target["non_emission_counts"] == {"filtered": 4}
    assert child.baseline["non_emission_counts"] == {"filtered": 2}
    empty = {}
    ChildDiagnostics(empty).update({"initial_request_profile": "youtube_ios"})
    assert empty == {"initial_request_profile": "youtube_ios"}


def test_channel_child_first_interrupt_unwinds_without_reacquiring(monkeypatch):
    closed, discoveries = [], []

    def source():
        try:
            yield _item()
            raise KeyboardInterrupt
        finally:
            closed.append(True)

    def discover(**_kwargs):
        discoveries.append(True)
        return iter([{"video_id": "video", "title": "fixture", "video_type": "LIVE"}])

    with closing(YouTubeChatDownloader()) as provider:
        monkeypatch.setattr(provider, "get_user_videos", discover)
        monkeypatch.setattr(
            provider, "get_chat_by_video_id", lambda *_: Chat(source(), id="video")
        )
        chat = provider.get_chat_by_channel_id("fixture", ChatRequest())
        assert next(chat) == _item()
        with pytest.raises(KeyboardInterrupt):
            next(chat)
        chat.close()
    assert closed == [True]
    assert discoveries == [True]
