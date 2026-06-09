# SPDX-License-Identifier: MIT

from __future__ import annotations

from chat_downloader.sites.youtube.chat_users_router import (
    YouTubeChatUsersRouterMixin,
)


class _Router(YouTubeChatUsersRouterMixin):
    def __init__(self) -> None:
        self.calls = []

    def _get_chat_by_user_args(self, args, params):
        self.calls.append((args, params))
        return args, params


def test_chat_user_router_helper_methods_delegate_expected_keys() -> None:
    router = _Router()
    params = {"url": "https://example.test"}

    assert router.get_chat_by_channel_id("chan", params) == (
        {"channel_id": "chan"},
        params,
    )
    assert router.get_chat_by_user_id("user", params) == (
        {"user_id": "user"},
        params,
    )
    assert router.get_chat_by_custom_username("custom", params) == (
        {"custom_username": "custom"},
        params,
    )
    assert router.get_chat_by_handle("@handle", params) == (
        {"handle": "@handle"},
        params,
    )

    assert router.calls == [
        ({"channel_id": "chan"}, params),
        ({"user_id": "user"}, params),
        ({"custom_username": "custom"}, params),
        ({"handle": "@handle"}, params),
    ]
