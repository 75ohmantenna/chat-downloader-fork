# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, NoReturn, cast
from unittest.mock import MagicMock

import pytest

from chat_downloader.errors import InvalidParameter
from chat_downloader.runtime.session_lifecycle import (
    build_cookie,
    clear_all_cookies,
    close_sessions,
    create_session,
    propagate_cookie,
)
from chat_downloader.runtime.session_lifecycle import (
    get_cookie_value as get_session_cookie_value,
)
from chat_downloader.sites.base import BaseChatDownloader


def test_create_session_reuses_cached_session_and_copies_cookie_jar() -> None:
    owner = SimpleNamespace(
        config=SimpleNamespace(as_dict=dict),
        sessions={},
        _cookie_jar=[
            build_cookie(domain=".example.com", name="sid", value="abc")
        ],
    )

    class FakeSite(BaseChatDownloader):
        def __init__(self, **kwargs) -> None:
            self.session = SimpleNamespace(
                cookies=SimpleNamespace(set_cookie=MagicMock()),
            )

    first = create_session(owner, FakeSite)
    second = create_session(owner, FakeSite)

    assert first is second
    first.session.cookies.set_cookie.assert_called_once()


def test_create_session_logs_reuse_without_claiming_creation(
    monkeypatch,
) -> None:
    logs: list[tuple[str, str]] = []
    owner = SimpleNamespace(
        config=SimpleNamespace(as_dict=dict),
        sessions={},
        _cookie_jar=[],
    )

    class FakeSite(BaseChatDownloader):
        def __init__(self, **kwargs) -> None:
            self.session = SimpleNamespace(
                cookies=SimpleNamespace(set_cookie=MagicMock()),
            )

    monkeypatch.setattr(
        "chat_downloader.runtime.session_lifecycle.log",
        lambda level, message: logs.append((level, str(message))),
    )

    create_session(owner, FakeSite)
    create_session(owner, FakeSite)

    assert logs == [
        ("debug", "Created FakeSite session."),
        ("debug", "Reusing existing FakeSite session."),
    ]


def test_propagate_cookie_updates_local_jar_and_existing_sessions() -> None:
    session = MagicMock()
    owner = SimpleNamespace(
        _cookie_jar=MagicMock(),
        sessions={"Site": session},
    )

    propagate_cookie(owner, domain=".example.com", name="sid", value="abc")

    owner._cookie_jar.set_cookie.assert_called_once()
    session.set_cookie_value.assert_called_once()
    assert session.set_cookie_value.call_args.kwargs["rest"] == {}


@pytest.mark.parametrize("domain", ["", ".", "localhost", " example.com"])
def test_propagate_cookie_rejects_invalid_domain(domain: str) -> None:
    owner = SimpleNamespace(_cookie_jar=MagicMock(), sessions={})

    with pytest.raises(InvalidParameter, match="Invalid cookie domain"):
        propagate_cookie(owner, domain=domain, name="sid", value="abc")


def test_get_cookie_value_checks_local_jar_before_sessions() -> None:
    owner = SimpleNamespace(
        _cookie_jar=[SimpleNamespace(name="sid", value="local")],
        sessions={"Site": MagicMock()},
    )

    assert get_session_cookie_value(owner, "sid") == "local"
    owner.sessions["Site"].get_cookie_value.assert_not_called()


def test_get_cookie_value_falls_back_to_existing_sessions() -> None:
    session = MagicMock()
    session.get_cookie_value.return_value = "from-session"
    owner = SimpleNamespace(
        _cookie_jar=[],
        sessions={"Site": session},
    )

    assert (
        get_session_cookie_value(owner, "sid", default="missing")
        == "from-session"
    )
    session.get_cookie_value.assert_called_once_with("sid", default=None)


def test_clear_all_cookies_and_close_sessions_delegate_to_all_sessions() -> (
    None
):
    session = MagicMock()
    owner = SimpleNamespace(
        _cookie_jar=MagicMock(),
        config=SimpleNamespace(cookies="/tmp/cookies.txt"),
        sessions={"Site": session},
    )

    clear_all_cookies(owner)
    close_sessions(owner)

    owner._cookie_jar.clear.assert_called_once_with()
    session.clear_cookies.assert_called_once_with()
    session.close.assert_called_once_with()
    assert owner.config.cookies is None
    assert owner.sessions == {}


def test_close_sessions_continues_and_logs_if_a_close_fails() -> None:
    good_session = MagicMock()
    failing_session = MagicMock()
    failing_session.close.side_effect = RuntimeError("session failed")
    owner = SimpleNamespace(
        _cookie_jar=MagicMock(),
        config=SimpleNamespace(cookies="/tmp/cookies.txt"),
        sessions={"good": good_session, "bad": failing_session},
    )

    logs: list[tuple[str, str]] = []
    from chat_downloader.runtime import session_lifecycle

    original_log = session_lifecycle.log
    try:
        session_lifecycle.log = lambda level, message: logs.append(
            (level, str(message))
        )
        close_sessions(owner)
    finally:
        session_lifecycle.log = original_log

    good_session.close.assert_called_once_with()
    failing_session.close.assert_called_once_with()
    assert any(
        level == "warning" and "session failed" in message
        for level, message in logs
    )


def test_create_session_rejects_invalid_session_class() -> None:
    owner = SimpleNamespace(
        config=SimpleNamespace(as_dict=dict),
        sessions={},
        _cookie_jar=[],
    )

    with pytest.raises(TypeError, match="must extend BaseChatDownloader"):
        create_session(owner, cast("Any", object))


def test_create_session_rejects_base_session_class() -> None:
    owner = SimpleNamespace(
        config=SimpleNamespace(as_dict=dict),
        sessions={},
        _cookie_jar=[],
    )

    with pytest.raises(TypeError, match="may not be BaseChatDownloader"):
        create_session(owner, BaseChatDownloader)


def test_create_session_overwrite_replaces_existing_session() -> None:
    owner = SimpleNamespace(
        config=SimpleNamespace(
            as_dict=lambda: {"headers": {"User-Agent": "UA"}}
        ),
        sessions={},
        _cookie_jar=[],
    )

    class FakeSite(BaseChatDownloader):
        counter = 0

        def __init__(self, **kwargs) -> None:
            FakeSite.counter += 1
            self.instance_id = FakeSite.counter
            self.closed = False
            self.kwargs = kwargs
            self.session = SimpleNamespace(
                cookies=SimpleNamespace(set_cookie=MagicMock()),
            )

        def close(self) -> None:
            self.closed = True

    first = create_session(owner, FakeSite)
    second = create_session(owner, FakeSite, overwrite=True)

    assert first is not second
    assert cast("Any", first).closed is True
    assert cast("Any", second).closed is False
    assert cast("Any", second).instance_id == 2
    assert cast("Any", second).kwargs == {"headers": {"User-Agent": "UA"}}


def test_create_session_overwrite_warns_and_replaces_when_existing_close_fails(
    monkeypatch,
) -> None:
    logs: list[tuple[str, str]] = []
    owner = SimpleNamespace(
        config=SimpleNamespace(
            as_dict=lambda: {"headers": {"User-Agent": "UA"}}
        ),
        sessions={},
        _cookie_jar=[],
    )

    class FakeSite(BaseChatDownloader):
        counter = 0

        def __init__(self, **kwargs) -> None:
            FakeSite.counter += 1
            self.instance_id = FakeSite.counter
            self.closed = False
            self.kwargs = kwargs
            self.session = SimpleNamespace(
                cookies=SimpleNamespace(set_cookie=MagicMock()),
            )

        def close(self) -> None:
            self.closed = True

    first = create_session(owner, FakeSite)

    def bad_close() -> NoReturn:
        raise RuntimeError("existing close failed")

    first.close = bad_close

    monkeypatch.setattr(
        "chat_downloader.runtime.session_lifecycle.log",
        lambda level, message: logs.append((level, str(message))),
    )

    second = create_session(owner, FakeSite, overwrite=True)

    assert first is not second
    assert first.closed is False
    assert any(
        level == "warning" and "existing close failed" in message
        for level, message in logs
    )
    assert cast("Any", second).instance_id == 2
    assert cast("Any", second).kwargs == {"headers": {"User-Agent": "UA"}}
