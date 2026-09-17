# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock


def wrap(path, value):
    for key in reversed(path.split(".")):
        value = {key: value}
    return value


def response(actions=(), *, continuations=None, **extra):
    chat = {"actions": list(actions)}
    if continuations is not None:
        chat["continuations"] = continuations
    return {**wrap("continuationContents.liveChatContinuation", chat), **extra}


def watch_chat(renderer):
    return wrap(
        "contents.twoColumnWatchNextResults.conversationBar.liveChatRenderer", renderer
    )


def submenu_item(token, endpoint="reloadContinuationData", title=None):
    if endpoint == "reloadContinuationData":
        item = wrap(f"continuation.{endpoint}.continuation", token)
    else:
        key = "token" if endpoint == "continuationCommand" else "continuation"
        item = wrap(f"continuationEndpoint.{endpoint}.{key}", token)
    if title is not None:
        item["title"] = title
    return item


def item_action(renderer, content):
    return wrap(f"addChatItemAction.item.{renderer}", content)


def video_info(status="live", *, top="top-token", live="live-token", **extra):
    return {
        "continuation_info": {"Top chat": top, "Live chat": live},
        "status": status,
        **extra,
    }


def patch(monkeypatch, target, value):
    monkeypatch.setattr(f"chat_downloader.sites.youtube.{target}", value)


def returns(monkeypatch, target, value):
    patch(monkeypatch, target, Mock(return_value=value))


def http_response(status_code=200, payload=None, text=""):
    return SimpleNamespace(
        status_code=status_code, text=text, json=Mock(return_value=payload)
    )


class Downloader:
    def __init__(self):
        self.session = SimpleNamespace(headers={})
        self._session_post = object()
        self.invalid_type_checks = []
        self.header_updates = []
        self.applied_profiles = []
        self._request_profile = "youtube_web"
        self._auto_profile_fallback = True

    def check_for_invalid_types(self, message_types, valid_types):
        self.invalid_type_checks.append((message_types, valid_types))

    def update_session_headers(self, headers):
        self.header_updates.append(headers)
        self.session.headers.update(headers)

    def replace_session_headers(self, headers, managed_names):
        for name in managed_names:
            self.session.headers.pop(name, None)
        self.update_session_headers(headers)

    def apply_request_profile(self, profile_name):
        self.applied_profiles.append(profile_name)
        self._request_profile = profile_name
        return True
