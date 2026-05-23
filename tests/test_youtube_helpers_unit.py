# SPDX-License-Identifier: MIT

from chat_downloader.sites.youtube.helpers import (
    _extract_browse_continuation_token,
    _extract_browse_continuation_token_from_response,
    _extract_menu_continuation_token,
    _safe_get_dict,
    extract_chat_submenu_continuations,
)


def test_safe_get_dict_returns_empty_dict_for_missing_or_falsy_values() -> None:
    assert _safe_get_dict({}, "missing") == {}
    assert _safe_get_dict({"value": None}, "value") == {}
    assert _safe_get_dict({"value": 0}, "value") == {}
    assert _safe_get_dict({"value": {"ok": True}}, "value") == {"ok": True}


def test_extract_browse_continuation_token_handles_invalid_and_missing_inputs() -> (
    None
):
    assert _extract_browse_continuation_token(None) is None
    assert (
        _extract_browse_continuation_token(
            [{}, {"continuationItemRenderer": {}}]
        )
        is None
    )
    assert (
        _extract_browse_continuation_token(
            [
                {},
                {
                    "continuationItemRenderer": {
                        "continuationEndpoint": {
                            "continuationCommand": {"token": "browse-token"},
                        },
                    },
                },
            ],
        )
        == "browse-token"
    )


def test_extract_menu_continuation_token_supports_all_known_shapes() -> None:
    assert _extract_menu_continuation_token(None) is None
    assert (
        _extract_menu_continuation_token(
            {
                "continuation": {
                    "reloadContinuationData": {"continuation": "reload"}
                }
            },
        )
        == "reload"
    )
    assert (
        _extract_menu_continuation_token(
            {
                "continuation": {
                    "continuationEndpoint": {
                        "continuationCommand": {"token": "nested-command"},
                    },
                },
            },
        )
        == "nested-command"
    )
    assert (
        _extract_menu_continuation_token(
            {
                "continuation": {
                    "continuationEndpoint": {
                        "getLiveChatEndpoint": {"continuation": "nested-live"},
                    },
                },
            },
        )
        == "nested-live"
    )
    assert (
        _extract_menu_continuation_token(
            {
                "continuationEndpoint": {
                    "continuationCommand": {"token": "top-command"}
                }
            },
        )
        == "top-command"
    )
    assert (
        _extract_menu_continuation_token(
            {
                "continuationEndpoint": {
                    "getLiveChatEndpoint": {"continuation": "top-live"},
                },
            },
        )
        == "top-live"
    )


def test_extract_chat_submenu_continuations_uses_fallback_labels_and_ignores_invalid_items() -> (
    None
):
    yt_data = {
        "continuationContents": {
            "liveChatContinuation": {
                "header": {
                    "liveChatHeaderRenderer": {
                        "viewSelector": {
                            "sortFilterSubMenuRenderer": {
                                "subMenuItems": [
                                    "skip-me",
                                    {
                                        "continuationEndpoint": {
                                            "continuationCommand": {
                                                "token": "top"
                                            },
                                        },
                                    },
                                    {
                                        "title": "Live chat",
                                        "continuationEndpoint": {
                                            "getLiveChatEndpoint": {
                                                "continuation": "live",
                                            },
                                        },
                                    },
                                    {},
                                ],
                            },
                        },
                    },
                },
            },
        },
    }

    assert extract_chat_submenu_continuations(
        yt_data,
        fallback_labels=["Top chat", "Replay"],
    ) == {
        "Top chat": "top",
        "Live chat": "live",
    }


def test_extract_chat_submenu_continuations_rejects_non_list_menu() -> None:
    assert (
        extract_chat_submenu_continuations(
            {
                "continuationContents": {
                    "liveChatContinuation": {
                        "header": {
                            "liveChatHeaderRenderer": {
                                "viewSelector": {
                                    "sortFilterSubMenuRenderer": {
                                        "subMenuItems": {"not": "a-list"},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        )
        == {}
    )


def test_extract_browse_continuation_token_from_response_checks_fallback_locations() -> (
    None
):
    assert (
        _extract_browse_continuation_token_from_response(
            {
                "onResponseReceivedEndpoints": [
                    {
                        "reloadContinuationItemsCommand": {
                            "continuationItems": [
                                {
                                    "continuationItemRenderer": {
                                        "continuationEndpoint": {
                                            "continuationCommand": {
                                                "token": "resp-token"
                                            },
                                        },
                                    },
                                },
                            ],
                        },
                    },
                ],
            },
        )
        == "resp-token"
    )
    assert _extract_browse_continuation_token_from_response({}) is None
