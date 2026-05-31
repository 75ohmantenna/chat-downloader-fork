# SPDX-License-Identifier: MIT

from chat_downloader import metadata


def test_fork_metadata_credits_75ohmantenna() -> None:
    assert metadata.__author__ == "75ohmantenna"
    assert metadata.__maintainer__ == "75ohmantenna"
    assert "75ohmantenna" in metadata.__copyright__
    assert metadata.__url__ == (
        "https://github.com/75ohmantenna/chat-downloader-fork"
    )


def test_metadata_preserves_upstream_attribution() -> None:
    assert metadata.__original_author__ == "xenova"
    assert (
        metadata.__upstream_url__ == "https://github.com/xenova/chat-downloader"
    )
    assert "xenova" in metadata.__copyright__
