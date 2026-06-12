# SPDX-License-Identifier: MIT

from __future__ import annotations

import os
import tempfile

import pytest

from chat_downloader import ChatDownloader
from chat_downloader.output.continuous_write import _WRITER_CLASSES

pytestmark = pytest.mark.network


def test_writers() -> None:
    test_urls = [
        # Use a past broadcast (VOD) so the JSON->JSONL live-stream redirect
        # doesn't fire
        "https://www.youtube.com/watch?v=wXspodtIxYU",
    ]

    downloader = ChatDownloader()

    with tempfile.TemporaryDirectory() as tmp:
        for index, test_url in enumerate(test_urls):
            # Test types of writers
            for extension in _WRITER_CLASSES:
                path = os.path.join(tmp, f"test_{index}.{extension}")

                chat = list(
                    downloader.get_chat(test_url, max_messages=10, output=path),
                )

                # ensure output is non-empty
                size = os.stat(path).st_size
                assert size != 0

                # Test appending
                chat = list(
                    downloader.get_chat(
                        test_url,
                        max_messages=10,
                        output=path,
                        overwrite=False,
                    ),
                )

                assert os.stat(path).st_size > size

                # Test file name formatting
                formatting_path = os.path.join(tmp, f"{{id}}_{{title}}.{extension}")
                chat = downloader.get_chat(
                    test_url,
                    max_messages=10,
                    output=formatting_path,
                )
                list(chat)  # Iterate over items

                assert os.path.exists(chat._output_dispatcher.writers[0].file_name)
