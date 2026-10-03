# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from chat_downloader.formatting import ItemFormatter
from chat_downloader.sites.kick.constants import (
    CHAT_MESSAGE_EVENT,
    MESSAGE_DELETED_EVENT,
    PUSHER_SUBSCRIPTION_SUCCEEDED,
)
from chat_downloader.sites.kick.deleted_message_cache import _DeletedMessageCache
from tests.kick_helpers import (
    FakeResponse,
    collect_live_chat,
    fixture_frame,
    live_chat,
    live_clock,
    message_page,
    pusher_frame,
    raw_message,
    recovery_session,
)


def _remember(cache, message_id, text="Original text", /, **fields):
    message = {
        "message_id": message_id,
        "message_type": "text_message",
        "message": text,
        **fields,
    }
    cache.observe(message)
    return message


def _deletion(cache, message_id):
    message = {
        "message_type": "message_deleted",
        "message": "",
        "metadata": {"deleted_message_id": message_id},
    }
    cache.observe(message)
    return message


@pytest.mark.parametrize("source", ["live", "preloaded", "reconnect", "backfill"])
@pytest.mark.parametrize("groups", [["all"], ["moderation"]])
def test_live_deletion_uses_original_text_from_all_sources(source, groups):
    original = raw_message(
        "ai-deleted-message",
        "2026-01-01T00:00:08Z",
        "Original [emote:123:hello]",
        sender={"id": 42, "username": "Author"},
    )
    text_frame = pusher_frame(CHAT_MESSAGE_EVENT, original)
    deletion_frame = fixture_frame(
        MESSAGE_DELETED_EVENT, "message_deleted_event_ai.json"
    )
    subscription_frame = pusher_frame(PUSHER_SUBSCRIPTION_SUCCEEDED, {})
    responses = []
    session = None
    if source == "preloaded":
        responses = [FakeResponse(200, message_page([original]))]
        batches = [[deletion_frame]]
    elif source == "reconnect":
        session = recovery_session([])
        batches = [
            [text_frame, ConnectionError("drop")],
            [subscription_frame, deletion_frame],
        ]
    elif source == "backfill":
        session = recovery_session([original])
        batches = [[ConnectionError("drop")], [subscription_frame, deletion_frame]]
    else:
        batches = [[text_frame, deletion_frame]]

    with (
        live_clock(return_value=1_767_225_610_000_000_000),
        live_chat(
            batches,
            *responses,
            session=session,
            request_kwargs={"message_groups": groups},
        ) as chat,
    ):
        messages = list(chat.chat)

    deleted = messages[-1]
    assert deleted["message_id"] == "ai-delete-event"
    assert deleted["message"] == ""
    assert deleted["metadata"] == {
        "deleted_message_id": "ai-deleted-message",
        "ai_moderated": True,
        "violated_rules": ["hate", "harassment"],
        "deleted_message_text": "Original :hello:",
        "deleted_message_author": "Author",
    }
    assert ItemFormatter().format(deleted, format_name="kick") == (
        "2026-01-01 00:00:10 [received] | "
        "[Message deleted: ai-deleted-message] [AI moderated] "
        "(rules: hate, harassment) Author: Original :hello:"
    )
    if groups == ["moderation"]:
        assert messages == [deleted]


def test_live_deletion_cache_is_scoped_to_one_chat():
    text_frame = pusher_frame(CHAT_MESSAGE_EVENT, raw_message("original"))
    deletion_frame = pusher_frame(
        MESSAGE_DELETED_EVENT,
        {"id": "deletion", "message": {"id": "original"}},
    )
    collect_live_chat([[text_frame]])
    _, messages = collect_live_chat([[deletion_frame]])
    assert messages[0]["metadata"] == {"deleted_message_id": "original"}


def test_count_limit_evicts_oldest_without_refreshing_duplicate():
    cache = _DeletedMessageCache(limit=2)
    _remember(cache, "oldest")
    _remember(cache, "middle")
    _remember(cache, "oldest", "Duplicate text")
    _remember(cache, "newest")
    assert _deletion(cache, "oldest")["metadata"] == {"deleted_message_id": "oldest"}
    for message_id in ("middle", "newest"):
        assert _deletion(cache, message_id)["metadata"]["deleted_message_text"] == (
            "Original text"
        )


def test_byte_budget_evicts_multiple_entries_and_accounts_for_unicode():
    cache = _DeletedMessageCache(max_bytes=1800)
    for message_id in ("one", "two", "three"):
        _remember(cache, message_id, "a" * 400)
    _remember(cache, "emoji", "\U0001f600" * 400)
    for message_id in ("one", "two", "three"):
        assert "deleted_message_text" not in _deletion(cache, message_id)["metadata"]
    assert _deletion(cache, "emoji")["metadata"]["deleted_message_text"] == (
        "\U0001f600" * 400
    )
    _remember(cache, "after", "Small message")
    assert "deleted_message_text" not in _deletion(cache, "emoji")["metadata"]
    assert _deletion(cache, "after")["metadata"]["deleted_message_text"] == (
        "Small message"
    )


def test_oversized_message_does_not_evict_retained_text():
    cache = _DeletedMessageCache(max_bytes=1024)
    _remember(cache, "kept")
    _remember(cache, "oversized", "a" * 1024)
    assert "deleted_message_text" not in _deletion(cache, "oversized")["metadata"]
    assert _deletion(cache, "kept")["metadata"]["deleted_message_text"] == (
        "Original text"
    )


@pytest.mark.parametrize("limits", [{"limit": 0}, {"max_bytes": 0}])
def test_disabled_cache_leaves_deletion_unchanged(limits):
    cache = _DeletedMessageCache(**limits)
    _remember(cache, "original")
    assert _deletion(cache, "original")["metadata"] == {
        "deleted_message_id": "original"
    }


@pytest.mark.parametrize(
    ("author", "expected"),
    [
        ({"display_name": "Display", "name": "slug"}, "Display"),
        ({"display_name": "", "name": "slug"}, "slug"),
        ({}, None),
    ],
)
def test_cached_author_is_a_name_snapshot(author, expected):
    cache = _DeletedMessageCache()
    original = _remember(cache, "original", author=author)
    original["message"] = "Changed by caller"
    author["display_name"] = "Changed by caller"
    deleted = _deletion(cache, "original")
    assert deleted["metadata"]["deleted_message_text"] == "Original text"
    assert deleted["metadata"].get("deleted_message_author") == expected
    suffix = f" {expected}:" if expected else ""
    assert ItemFormatter().format(deleted, format_name="kick") == (
        f"[Message deleted: original]{suffix} Original text"
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"message_id": ""},
        {"message": ""},
        {"message_type": "pinned_message"},
    ],
)
def test_non_text_and_empty_messages_do_not_consume_cache_capacity(fields):
    cache = _DeletedMessageCache(limit=1)
    _remember(cache, "kept")
    _remember(cache, "ignored", **fields)
    assert _deletion(cache, "kept")["metadata"]["deleted_message_text"] == (
        "Original text"
    )


def test_deletion_without_target_is_unchanged():
    cache = _DeletedMessageCache()
    _remember(cache, "original")
    deleted = {"message_type": "message_deleted", "message": ""}
    cache.observe(deleted)
    assert deleted == {"message_type": "message_deleted", "message": ""}


def test_deleted_text_and_author_render_with_safe_line_separators():
    cache = _DeletedMessageCache()
    _remember(
        cache,
        "original",
        "Original\nForged\x1b",
        author={"display_name": "Author\rForged\x1b"},
    )
    assert ItemFormatter().format(_deletion(cache, "original"), format_name="kick") == (
        r"[Message deleted: original] Author\rForged: Original\nForged"
    )
