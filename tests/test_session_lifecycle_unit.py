# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import Any, NoReturn, cast
from unittest.mock import MagicMock

import pytest

from chat_downloader import ChatDownloader
from chat_downloader.errors import InvalidParameter
from chat_downloader.sites.base import BaseChatDownloader


class _FakeSite(BaseChatDownloader):
    created = 0

    def __init__(self, **kwargs: Any) -> None:
        type(self).created += 1
        self.instance_id = type(self).created
        self.kwargs = kwargs
        super().__init__(**kwargs)


def test_downloader_reuses_overwrites_and_replaces_closed_sessions() -> None:
    _FakeSite.created = 0
    downloader = ChatDownloader(headers={"User-Agent": "UA"})

    first = downloader.create_session(_FakeSite)
    assert downloader.create_session(_FakeSite) is first
    assert downloader.get_session(_FakeSite) is first

    second = downloader.create_session(_FakeSite, overwrite=True)
    assert second is not first
    assert first._session_closed is True
    assert cast("_FakeSite", second).kwargs["headers"] == {"User-Agent": "UA"}

    second.close()
    third = downloader.create_session(_FakeSite)
    assert third is not second
    assert cast("_FakeSite", third).instance_id == 3


def test_downloader_rejects_same_name_different_site_class() -> None:
    first_class = type("SameNameSite", (BaseChatDownloader,), {})
    second_class = type("SameNameSite", (BaseChatDownloader,), {})
    downloader = ChatDownloader()
    first = downloader.create_session(first_class)

    with pytest.raises(TypeError, match="Session name collision"):
        downloader.create_session(second_class)

    assert downloader.get_session(second_class) is None
    assert downloader.get_session(first_class) is first


def test_downloader_cookie_state_precedes_and_propagates_to_sessions() -> None:
    downloader = ChatDownloader()
    first = downloader.create_session(_FakeSite)

    downloader.set_cookie_value(".example.com", "sid", "local", rest={"x": "1"})
    assert downloader.get_cookie_value("sid") == "local"
    assert first.get_cookie_value("sid") == "local"

    second = downloader.create_session(_FakeSite, overwrite=True)
    assert second.get_cookie_value("sid") == "local"


@pytest.mark.parametrize("domain", ["", ".", "localhost", " example.com"])
def test_downloader_cookie_rejects_invalid_domain(domain: str) -> None:
    with pytest.raises(InvalidParameter, match="Invalid cookie domain"):
        ChatDownloader().set_cookie_value(domain, "sid", "abc")


def test_downloader_cookie_lookup_falls_back_across_site_sessions() -> None:
    downloader = ChatDownloader()
    first = MagicMock()
    first.get_cookie_value.return_value = None
    second = MagicMock()
    second.get_cookie_value.return_value = "from-second"
    downloader.sessions.update({"first": first, "second": second})

    assert downloader.get_cookie_value("sid", "missing") == "from-second"
    first.get_cookie_value.assert_called_once_with("sid", default=None)
    second.get_cookie_value.assert_called_once_with("sid", default=None)


def test_clear_cookies_disables_source_and_close_empties_compatibility_state() -> None:
    downloader = ChatDownloader(cookies=None)
    session = downloader.create_session(_FakeSite)
    downloader.set_cookie_value(".example.com", "sid", "abc")

    downloader.clear_cookies()
    assert downloader.config.cookies is None
    assert downloader.get_cookie_value("sid") is None
    assert session.get_cookie_value("sid") is None

    downloader.close()
    assert downloader.sessions == {}
    downloader.close()


def test_pool_rejects_invalid_and_base_site_classes() -> None:
    downloader = ChatDownloader()
    with pytest.raises(TypeError, match="must extend BaseChatDownloader"):
        downloader.create_session(cast("Any", object))
    with pytest.raises(TypeError, match="may not be BaseChatDownloader"):
        downloader.create_session(BaseChatDownloader)


def test_overwrite_logs_close_failure_and_still_replaces(monkeypatch) -> None:
    downloader = ChatDownloader()
    first = downloader.create_session(_FakeSite)
    logs: list[tuple[str, str]] = []

    def bad_close() -> NoReturn:
        raise RuntimeError("existing close failed")

    first.close = bad_close
    monkeypatch.setattr(
        "chat_downloader.runtime.session_lifecycle.log",
        lambda level, message: logs.append((level, str(message))),
    )

    second = downloader.create_session(_FakeSite, overwrite=True)
    assert second is not first
    assert any("existing close failed" in message for _, message in logs)


def test_close_continues_after_one_site_failure(monkeypatch) -> None:
    downloader = ChatDownloader()
    good = MagicMock()
    bad = MagicMock()
    bad.close.side_effect = RuntimeError("session failed")
    downloader.sessions.update({"good": good, "bad": bad})
    logs: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "chat_downloader.runtime.session_lifecycle.log",
        lambda level, message: logs.append((level, str(message))),
    )

    downloader.close()

    good.close.assert_called_once_with()
    bad.close.assert_called_once_with()
    assert downloader.sessions == {}
    assert any("session failed" in message for _, message in logs)
