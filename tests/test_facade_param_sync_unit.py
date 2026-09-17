# SPDX-License-Identifier: MIT

"""Keep get_chat() parameter names/defaults aligned with ChatRequest."""

from __future__ import annotations

import inspect
from dataclasses import MISSING, fields

from chat_downloader.chat_downloader import ChatDownloader
from chat_downloader.models import CHAT_PARAM_NAMES, ChatRequest
from chat_downloader.sites.models import SiteDefault


def test_get_chat_signature_matches_chatrequest() -> None:
    params = dict(inspect.signature(ChatDownloader.get_chat).parameters)
    params.pop("self", None)
    assert set(params) == set(CHAT_PARAM_NAMES)
    for field in fields(ChatRequest):
        actual = params[field.name].default
        # The facade normalizes the absent URL sentinel to the request's empty URL.
        if field.name == "url" or actual is inspect.Parameter.empty:
            continue
        expected = (
            field.default if field.default is not MISSING else field.default_factory()
        )
        if isinstance(expected, SiteDefault) and isinstance(actual, SiteDefault):
            assert expected.name == actual.name, field.name
        else:
            assert actual == expected, field.name
