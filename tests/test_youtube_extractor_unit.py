# SPDX-License-Identifier: MIT

from __future__ import annotations

from chat_downloader.sites.youtube import extractor as yt_extractor
from chat_downloader.sites.youtube import helpers as yt_helpers
from chat_downloader.sites.youtube.helpers import (
    _extract_browse_continuation_token_from_response,
)


def test_extract_browse_continuation_token_from_response_reload_endpoints() -> None:
    yt_info = {
        "onResponseReceivedEndpoints": [
            {
                "reloadContinuationItemsCommand": {
                    "continuationItems": [
                        {
                            "continuationItemRenderer": {
                                "continuationEndpoint": {
                                    "continuationCommand": {"token": "NEXT_TOKEN"},
                                },
                            },
                        },
                    ],
                },
            },
        ],
    }

    assert _extract_browse_continuation_token_from_response(yt_info) == "NEXT_TOKEN"


def test_extract_browse_continuation_token_from_response_playlist_continuation_contents() -> (  # noqa: E501
    None
):
    yt_info = {
        "continuationContents": {
            "playlistVideoListContinuation": {
                "contents": [
                    {
                        "continuationItemRenderer": {
                            "continuationEndpoint": {
                                "continuationCommand": {"token": "PLAYLIST_NEXT"},
                            },
                        },
                    },
                ],
            },
        },
    }

    assert _extract_browse_continuation_token_from_response(yt_info) == "PLAYLIST_NEXT"


def test_extract_browse_continuation_token_from_response_reload_actions() -> None:
    yt_info = {
        "onResponseReceivedActions": [
            {
                "reloadContinuationItemsCommand": {
                    "continuationItems": [
                        {
                            "continuationItemRenderer": {
                                "continuationEndpoint": {
                                    "continuationCommand": {"token": "ACTION_TOKEN"},
                                },
                            },
                        },
                    ],
                },
            },
        ],
    }

    assert _extract_browse_continuation_token_from_response(yt_info) == "ACTION_TOKEN"


def test_extract_chat_submenu_continuations_uses_fallback_for_unlabeled_items() -> None:
    yt_info = {
        "continuationContents": {
            "liveChatContinuation": {
                "header": {
                    "liveChatHeaderRenderer": {
                        "viewSelector": {
                            "sortFilterSubMenuRenderer": {
                                "subMenuItems": [
                                    {
                                        "title": "Top chat",
                                        "continuation": {
                                            "reloadContinuationData": {
                                                "continuation": "LABELED_TOKEN",
                                            },
                                        },
                                    },
                                    {
                                        "continuation": {
                                            "continuationEndpoint": {
                                                "continuationCommand": {
                                                    "token": "UNLABELED_TOKEN",
                                                },
                                            },
                                        },
                                    },
                                    {
                                        "title": "",
                                        "continuation": {
                                            "continuationEndpoint": {
                                                "getLiveChatEndpoint": {
                                                    "continuation": "SKIP_TITLE_EMPTY",
                                                },
                                            },
                                        },
                                    },
                                    None,
                                ]
                            }
                        }
                    }
                }
            }
        }
    }

    assert yt_helpers.extract_chat_submenu_continuations(
        yt_info, fallback_labels=["Fallback"]
    ) == {
        "Top chat": "LABELED_TOKEN",
        "Fallback": "UNLABELED_TOKEN",
    }


def test_extract_browse_continuation_token_non_list_input() -> None:
    assert yt_helpers._extract_browse_continuation_token(None) is None


def test_has_auth_cookies_requires_login_info_and_any_sapisid(
    monkeypatch,
) -> None:
    downloader = yt_extractor.YouTubeChatDownloader()

    monkeypatch.setattr(
        downloader,
        "get_cookie_value",
        lambda name: "login" if name == "LOGIN_INFO" else None,
    )
    monkeypatch.setattr(
        yt_extractor,
        "_get_sid_cookies",
        lambda _owner: (None, "__Secure-1PAPISID", None),
    )

    assert downloader._has_auth_cookies is True


def test_has_auth_cookies_is_false_without_login_info(monkeypatch) -> None:
    downloader = yt_extractor.YouTubeChatDownloader()

    monkeypatch.setattr(downloader, "get_cookie_value", lambda _name: None)
    monkeypatch.setattr(
        yt_extractor,
        "_get_sid_cookies",
        lambda _owner: ("SAPISID", None, None),
    )

    assert downloader._has_auth_cookies is False


def test_is_live_status_recognizes_live_and_post_live() -> None:
    downloader = yt_extractor.YouTubeChatDownloader()

    assert downloader.is_live_status("live") is True
    assert downloader.is_live_status("post_live") is True
    assert downloader.is_live_status("past") is False
    assert downloader.is_live_status(None) is False


def test_resolve_live_format_maps_base_names_to_live_variants() -> None:
    downloader = yt_extractor.YouTubeChatDownloader()

    assert downloader.resolve_live_format("default") == "youtube_live_default"
    assert downloader.resolve_live_format("youtube") == "youtube_live_default"
    assert downloader.resolve_live_format("24_hour") == "youtube_live_24_hour"
    assert downloader.resolve_live_format("12_hour") == "youtube_live_12_hour"
    # Unknown/custom names are passed through unchanged.
    assert downloader.resolve_live_format("custom") == "custom"
