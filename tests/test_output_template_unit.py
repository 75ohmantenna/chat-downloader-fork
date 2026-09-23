# SPDX-License-Identifier: MIT

"""Output path templates reject unsafe fields before provider requests."""

from __future__ import annotations

import pytest

from chat_downloader.errors import InvalidParameter
from chat_downloader.models import ChatRequest
from chat_downloader.sites.output_dispatch import _expand_output_file_name


@pytest.mark.parametrize(
    "path",
    [
        "chat_{date}.txt",
        "chat_{}.txt",
        "chat_{0}.txt",
        "chat_{title!r}.txt",
        "chat_{title:>1000000000}.txt",
        "chat_{title.name}.txt",
        "chat_{title.txt",
    ],
)
def test_invalid_output_template_is_rejected_at_request_boundary(path: str) -> None:
    with pytest.raises(InvalidParameter, match=r"output path|Output paths"):
        ChatRequest(output=path)


def test_literal_braces_and_supported_fields_expand() -> None:
    path = "{{archive}}_{title}_{id}.txt"
    ChatRequest(output=path)
    assert _expand_output_file_name(path, title="Example", video_id="123") == (
        "{archive}_Example_123.txt"
    )
