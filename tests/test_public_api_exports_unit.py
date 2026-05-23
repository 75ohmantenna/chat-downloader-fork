# SPDX-License-Identifier: MIT

import chat_downloader
from chat_downloader import (
    ContinuousFileWriter,
    ContinuousWriter,
    ItemFormatter,
    TwitchChatDownloader,
    TwitchError,
    YouTubeChatDownloader,
    get_all_sites,
)
from chat_downloader.errors import __all__ as error_exports
from chat_downloader.models import __all__ as model_exports
from chat_downloader.sites import __all__ as site_exports


def test_top_level_package_re_exports_public_consumer_types() -> None:
    assert chat_downloader.ContinuousFileWriter is ContinuousFileWriter
    assert chat_downloader.ContinuousWriter is ContinuousWriter
    assert chat_downloader.ItemFormatter is ItemFormatter
    assert chat_downloader.TwitchChatDownloader is TwitchChatDownloader
    assert chat_downloader.TwitchError is TwitchError
    assert chat_downloader.YouTubeChatDownloader is YouTubeChatDownloader
    assert chat_downloader.get_all_sites is get_all_sites


def test_top_level___all___includes_public_consumer_types() -> None:
    exported = set(chat_downloader.__all__)

    assert "ContinuousFileWriter" in exported
    assert "ContinuousWriter" in exported
    assert "ItemFormatter" in exported
    assert "TwitchChatDownloader" in exported
    assert "TwitchError" in exported
    assert "YouTubeChatDownloader" in exported
    assert "get_all_sites" in exported


def test_sites_namespace_exports_twitch_error() -> None:
    assert "TwitchError" in site_exports


def test_errors_module_declares_explicit_public_surface() -> None:
    assert "ChatDownloaderError" in error_exports
    assert "FormatFileNotFound" in error_exports
    assert "VideoUnavailable" in error_exports


def test_models_module_declares_explicit_public_surface() -> None:
    assert "ChatRequest" in model_exports
    assert "DownloaderConfig" in model_exports
    assert "coerce_chat_request" in model_exports
    assert "RUN_PARAM_NAMES" in model_exports
    assert "INIT_PARAM_NAMES" in model_exports
    assert "CHAT_PARAM_NAMES" in model_exports
