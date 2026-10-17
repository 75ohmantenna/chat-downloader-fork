# SPDX-License-Identifier: MIT

"""Console-only compaction through real runtime, formatting and file writers."""

from __future__ import annotations

import json
from contextlib import closing
from copy import deepcopy
from unittest.mock import Mock

import pytest

from chat_downloader.formatting import ItemFormatter
from chat_downloader.models import ChatRequest
from chat_downloader.runtime.chat_pipeline import configure_chat
from chat_downloader.runtime.runner import create_message_callback, execute_run
from chat_downloader.sites._message_dedup import (
    _ConsoleState,
    _ConsoleStateDeduplicator,
)
from chat_downloader.sites.base import BaseChatDownloader
from chat_downloader.sites.kick.extractor import KickChatDownloader
from chat_downloader.sites.models import Chat


def update(
    identifier, *, prediction="p", votes=1, state="ACTIVE", kind="prediction_updated"
):
    return {
        "message_id": identifier,
        "message_type": kind,
        "message": kind.replace("_", " "),
        "received_timestamp": 1_700_000_000_000_000,
        "metadata": {
            "data": {
                "prediction": {
                    "id": prediction,
                    "title": "Who?",
                    "state": state,
                    "outcomes": [{"id": "a", "title": "A", "vote_count": votes}],
                }
            }
        },
    }


def stream():
    return [
        update("one"),
        {"message_id": "chat", "message_type": "text_message", "message": "hello"},
        update("repeat"),
        update("change", votes=2),
        update("back-to-one"),
        update("other-prediction", prediction="other"),
        update("repeat-after-other"),
        update("created", kind="prediction_created"),
        update("repeat-after-created"),
        update("resolved", state="RESOLVED"),
        update("resolved-repeat", state="RESOLVED"),
    ]


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("quiet", [False, True])
def test_console_compaction_preserves_every_file_record_count_and_parity(
    tmp_path, capsys, compact, quiet
):
    messages = stream()
    original = deepcopy(messages)

    class RecordedDownloader(KickChatDownloader):
        def get_chat(self, **kwargs):
            chat = Chat(iter(deepcopy(messages)))
            configure_chat(chat, ChatRequest.from_kwargs(**kwargs), self)
            return chat

    jsonl, txt = tmp_path / "capture.jsonl", tmp_path / "capture.txt"
    result = execute_run(
        RecordedDownloader,
        url="https://kick.com/example",
        format="kick",
        output=[str(jsonl), str(txt)],
        verify_output=True,
        compact_console=compact,
        quiet=quiet,
    )
    assert result.success
    assert result.parity_status == "passed"
    assert result.message_count == len(messages)
    assert result.message_type_counts["prediction_updated"] == 9
    assert [json.loads(line) for line in jsonl.read_text().splitlines()] == messages
    formatter = ItemFormatter()
    assert txt.read_text().splitlines() == [
        formatter.format(message, "kick") for message in messages
    ]
    selected = (
        [messages[index] for index in (0, 1, 3, 4, 5, 7, 9)] if compact else messages
    )
    assert capsys.readouterr().out.splitlines() == (
        [] if quiet else [formatter.format(message, "kick") for message in selected]
    )
    assert messages == original


def test_latest_state_not_global_history_and_separate_prediction_keys():
    cache = _ConsoleStateDeduplicator()
    assert cache.should_emit(_ConsoleState("p", {"votes": 1}))
    assert not cache.should_emit(_ConsoleState("p", {"votes": 1}))
    assert cache.should_emit(_ConsoleState("q", {"votes": 1}))
    assert cache.should_emit(_ConsoleState("p", {"votes": 2}))
    assert cache.should_emit(_ConsoleState("p", {"votes": 1}))
    assert not cache.should_emit(_ConsoleState("p", {"votes": 1}))
    assert cache.should_emit(None)


def test_canonical_payload_keys_and_mutation_are_handled_without_retention():
    cache = _ConsoleStateDeduplicator()
    payload = {"a": 1, "b": ["one", "two"]}
    assert cache.should_emit(_ConsoleState("p", payload))
    assert not cache.should_emit(_ConsoleState("p", {"b": ["one", "two"], "a": 1}))
    payload["b"].reverse()
    assert cache.should_emit(_ConsoleState("p", payload))
    assert payload == {"a": 1, "b": ["two", "one"]}


def test_lru_limit_evicts_state_and_refreshes_recent_identical_payload():
    cache = _ConsoleStateDeduplicator(max_states=2)
    assert cache.should_emit(_ConsoleState("a", 1))
    assert cache.should_emit(_ConsoleState("b", 1))
    assert not cache.should_emit(_ConsoleState("a", 1))
    assert cache.should_emit(_ConsoleState("c", 1))
    assert not cache.should_emit(_ConsoleState("a", 1))
    assert cache.should_emit(_ConsoleState("b", 1))
    assert len(cache._states) == 2
    assert all(len(key) == len(value) == 32 for key, value in cache._states.items())


@pytest.mark.parametrize("limit", [0, -1])
def test_disabled_cache_does_not_suppress(limit):
    cache = _ConsoleStateDeduplicator(max_states=limit)
    assert cache.should_emit(_ConsoleState("p", 1))
    assert cache.should_emit(_ConsoleState("p", 1))
    assert not cache._states


@pytest.mark.parametrize(
    "malformed",
    [object(), float("nan"), float("inf"), {1: "bad", "a": "bad"}, 10**5000],
    ids=["object", "nan", "infinity", "mixed-keys", "oversized-integer"],
)
def test_uncomparable_payload_remains_visible_and_resets_cache(malformed):
    cache = _ConsoleStateDeduplicator()
    assert cache.should_emit(_ConsoleState("p", 1))
    assert cache.should_emit(_ConsoleState("p", malformed))
    assert cache.should_emit(_ConsoleState("p", 1))


def test_circular_payload_does_not_break_display():
    payload = {}
    payload["recursive"] = payload
    cache = _ConsoleStateDeduplicator()
    assert cache.should_emit(_ConsoleState("p", payload))
    assert cache.should_emit(_ConsoleState("p", payload))


def test_provider_owns_selection_and_opaque_identifiers():
    with closing(KickChatDownloader()) as site, closing(BaseChatDownloader()) as base:
        assert base.get_console_state(update("1")) is None
        message = update("1", prediction=" p ")
        before = deepcopy(message)
        assert site.get_console_state(message) == _ConsoleState(
            "prediction: p ",
            message["metadata"]["data"]["prediction"],
        )
        assert message == before
        assert site.get_console_state(
            update("1", kind="prediction_created")
        ).always_display
        for kind in ("text_message", "subscription"):
            assert site.get_console_state(update("1", kind=kind)) is None


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"prediction": {}},
        {"prediction": {"id": True}},
        {"prediction": {"id": " "}},
    ],
)
def test_missing_or_invalid_prediction_identity_never_compacts(payload):
    with closing(KickChatDownloader()) as site:
        assert (
            site.get_console_state(
                {"message_type": "prediction_updated", "metadata": {"data": payload}}
            )
            is None
        )


def test_quiet_precedes_compaction_and_missing_capability_keeps_messages():
    chat = Chat()
    chat.print_formatted = Mock()
    callback = create_message_callback(chat=chat, quiet=False, compact_console=True)
    callback(update("1"))
    callback(update("2"))
    assert chat.print_formatted.call_count == 2
    chat.site = Mock()
    chat.site.get_console_state.side_effect = AssertionError(
        "quiet must not inspect state"
    )
    callback = create_message_callback(chat=chat, quiet=True, compact_console=True)
    callback(update("1"))
    assert chat.print_formatted.call_count == 2


def test_creation_notice_always_displays_and_refreshes_latest_state():
    cache = _ConsoleStateDeduplicator()
    assert cache.should_emit(_ConsoleState("p", {"votes": 1}))
    assert cache.should_emit(_ConsoleState("p", {"votes": 2}, always_display=True))
    assert cache.should_emit(_ConsoleState("p", {"votes": 2}, always_display=True))
    assert not cache.should_emit(_ConsoleState("p", {"votes": 2}))
    assert cache.should_emit(_ConsoleState("p", {"votes": 1}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", None),
        ("state", " "),
        ("outcomes", []),
        ("outcomes", [None]),
        ("outcomes", [{"title": "A", "vote_count": True}]),
        ("outcomes", [{"vote_count": 1}]),
    ],
)
def test_partial_prediction_updates_stay_visible_and_replace_cached_state(field, value):
    with closing(KickChatDownloader()) as site:
        cache = _ConsoleStateDeduplicator()
        original = update("original")
        assert cache.should_emit(site.get_console_state(original))
        partial = update("partial")
        partial["metadata"]["data"]["prediction"][field] = value
        assert cache.should_emit(site.get_console_state(partial))
        assert cache.should_emit(site.get_console_state(partial))
        assert cache.should_emit(site.get_console_state(original))
