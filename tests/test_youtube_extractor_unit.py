# SPDX-License-Identifier: MIT

from chat_downloader.sites.youtube import extractor as yt_extractor
from chat_downloader.sites.youtube import helpers as yt_helpers


def test_extract_browse_continuation_token_from_response_reload_endpoints() -> (
    None
):
    yt_info = {
        "onResponseReceivedEndpoints": [
            {
                "reloadContinuationItemsCommand": {
                    "continuationItems": [
                        {
                            "continuationItemRenderer": {
                                "continuationEndpoint": {
                                    "continuationCommand": {
                                        "token": "NEXT_TOKEN"
                                    },
                                },
                            },
                        },
                    ],
                },
            },
        ],
    }

    assert (
        yt_extractor._extract_browse_continuation_token_from_response(yt_info)
        == "NEXT_TOKEN"
    )


def test_extract_browse_continuation_token_from_response_playlist_continuation_contents() -> (
    None
):
    yt_info = {
        "continuationContents": {
            "playlistVideoListContinuation": {
                "contents": [
                    {
                        "continuationItemRenderer": {
                            "continuationEndpoint": {
                                "continuationCommand": {
                                    "token": "PLAYLIST_NEXT"
                                },
                            },
                        },
                    },
                ],
            },
        },
    }

    assert (
        yt_extractor._extract_browse_continuation_token_from_response(yt_info)
        == "PLAYLIST_NEXT"
    )


def test_extract_browse_continuation_token_from_response_reload_actions() -> (
    None
):
    yt_info = {
        "onResponseReceivedActions": [
            {
                "reloadContinuationItemsCommand": {
                    "continuationItems": [
                        {
                            "continuationItemRenderer": {
                                "continuationEndpoint": {
                                    "continuationCommand": {
                                        "token": "ACTION_TOKEN"
                                    },
                                },
                            },
                        },
                    ],
                },
            },
        ],
    }

    assert (
        yt_extractor._extract_browse_continuation_token_from_response(yt_info)
        == "ACTION_TOKEN"
    )


def test_extract_chat_submenu_continuations_uses_fallback_for_unlabeled_items() -> (
    None
):
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
