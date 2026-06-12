# SPDX-License-Identifier: MIT

"""Guard that get_chat() param names and defaults stay in sync with ChatRequest."""

from __future__ import annotations

import inspect
from dataclasses import MISSING
from dataclasses import fields as dc_fields

from chat_downloader.chat_downloader import ChatDownloader
from chat_downloader.models import ChatRequest
from chat_downloader.models._request import CHAT_PARAM_NAMES
from chat_downloader.sites.models import SiteDefault


def _get_chat_params() -> dict[str, inspect.Parameter]:
    params = dict(inspect.signature(ChatDownloader.get_chat).parameters)
    params.pop("self", None)
    return params


def test_get_chat_param_names_match_chatrequest() -> None:
    assert set(_get_chat_params()) == set(CHAT_PARAM_NAMES)


def test_get_chat_defaults_match_field_defaults() -> None:
    field_defaults: dict[str, object] = {}
    for f in dc_fields(ChatRequest):
        if f.default is not MISSING:
            field_defaults[f.name] = f.default
        elif f.default_factory is not MISSING:
            field_defaults[f.name] = f.default_factory()
    for name, p in _get_chat_params().items():
        expected = field_defaults[name]
        actual = p.default
        # url: facade uses None as "not provided" sentinel; dataclass uses "".
        # The facade normalises None→"" before constructing ChatRequest, so the
        # defaults legitimately differ.
        if name == "url":
            continue
        # url has no positional default in the signature; skip if empty sentinel
        if actual is inspect.Parameter.empty:
            continue
        if isinstance(expected, SiteDefault) and isinstance(actual, SiteDefault):
            assert expected.name == actual.name, name
        else:
            assert actual == expected, name
