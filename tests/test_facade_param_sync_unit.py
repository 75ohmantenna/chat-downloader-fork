# SPDX-License-Identifier: MIT

"""Keep the public get_chat() signature aligned with ChatRequest."""

from __future__ import annotations

import inspect
from dataclasses import MISSING, fields

from chat_downloader.chat_downloader import ChatDownloader
from chat_downloader.models import ChatRequest


def test_get_chat_signature_matches_chatrequest() -> None:
    params = dict(inspect.signature(ChatDownloader.get_chat).parameters)
    del params["self"]
    request_fields = {field.name: field for field in fields(ChatRequest)}
    assert params.keys() == request_fields.keys()

    # The facade normalizes its absent URL sentinel to the model's empty URL.
    url = params.pop("url")
    assert url.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert url.default is None
    assert request_fields["url"].default == ""

    for name, parameter in params.items():
        field = request_fields[name]
        expected = (
            field.default if field.default is not MISSING else field.default_factory()
        )
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, name
        assert parameter.default is not inspect.Parameter.empty, name
        assert parameter.default == expected, name
