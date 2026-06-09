# SPDX-License-Identifier: MIT

"""Session and cookie lifecycle helpers for ``ChatDownloader``."""

from __future__ import annotations

import contextlib
import dataclasses
from http.cookiejar import Cookie
from typing import TYPE_CHECKING, Any, cast

from chat_downloader.debugging import log
from chat_downloader.sites.base import BaseChatDownloader
from chat_downloader.sites.session import (
    _validate_cookie_domain,
)

if TYPE_CHECKING:
    from chat_downloader.models import DownloaderConfig
    from chat_downloader.runtime._protocols import ChatDownloaderProto


def build_cookie(
    *,
    domain: str,
    name: str,
    value: str,
    expire_time: int | None = None,
    port: str | None = None,
    path: str = "/",
    secure: bool = False,
    discard: bool = False,
    rest: dict[str, Any] | None = None,
) -> Cookie:
    """Build a ``Cookie`` using the downloader's compatibility shape."""
    _validate_cookie_domain(domain)
    cookie_rest = {} if rest is None else rest
    return Cookie(
        0,
        name,
        value,
        port,
        port is not None,
        domain,
        True,
        domain.startswith("."),
        path,
        True,
        secure,
        expire_time,
        discard,
        None,
        None,
        cookie_rest,
    )


def clear_all_cookies(owner: ChatDownloaderProto) -> None:
    """Clear the local cookie jar and all existing site sessions."""
    owner._cookie_jar.clear()
    for session in owner.sessions.values():
        session.clear_cookies()
    _disable_configured_cookie_source(owner)


def _disable_configured_cookie_source(owner: ChatDownloaderProto) -> None:
    """Prevent future sessions from reloading cookies from the original file."""
    config = getattr(owner, "config", None)
    if config is None or not hasattr(config, "cookies"):
        return

    if dataclasses.is_dataclass(config) and not isinstance(config, type):
        owner.config = cast(
            "DownloaderConfig", dataclasses.replace(config, cookies=None)
        )
        return

    with contextlib.suppress(AttributeError):
        config.cookies = None


def propagate_cookie(
    owner: ChatDownloaderProto,
    *,
    domain: str,
    name: str,
    value: str,
    expire_time: int | None = None,
    port: str | None = None,
    path: str = "/",
    secure: bool = False,
    discard: bool = False,
    rest: dict[str, Any] | None = None,
) -> None:
    """Store a cookie locally and mirror it to existing site sessions."""
    cookie_rest = {} if rest is None else rest
    cookie = build_cookie(
        domain=domain,
        name=name,
        value=value,
        expire_time=expire_time,
        port=port,
        path=path,
        secure=secure,
        discard=discard,
        rest=cookie_rest,
    )
    owner._cookie_jar.set_cookie(cookie)

    for session in owner.sessions.values():
        session.set_cookie_value(
            domain=domain,
            name=name,
            value=value,
            expire_time=expire_time,
            port=port,
            path=path,
            secure=secure,
            discard=discard,
            rest=cookie_rest,
        )


def get_cookie_value(
    owner: ChatDownloaderProto, name: str, default: Any = None
) -> Any:
    """Return a cookie value from the local jar or existing site sessions."""
    cookies_dict = {cookie.name: cookie.value for cookie in owner._cookie_jar}
    if name in cookies_dict:
        return cookies_dict[name]

    for session in owner.sessions.values():
        value = session.get_cookie_value(name, default=None)
        if value is not None:
            return value

    return default


def create_session(
    owner: ChatDownloaderProto,
    chat_downloader_class: type[Any],
    overwrite: bool = False,
) -> BaseChatDownloader:
    """Create or retrieve a site downloader session."""
    if not issubclass(chat_downloader_class, BaseChatDownloader):
        msg = (
            f"Unable to create session, class must extend "
            f"BaseChatDownloader. Class given: "
            f"{chat_downloader_class}"
        )
        raise TypeError(
            msg,
        )
    if chat_downloader_class == BaseChatDownloader:
        msg = "Unable to create session, class may not be BaseChatDownloader."
        raise TypeError(
            msg,
        )

    session_name = chat_downloader_class.__name__
    existing_session = owner.sessions.get(session_name)
    if existing_session is not None and not overwrite:
        log("debug", f"Reusing existing {session_name} session.")
        return existing_session

    if overwrite and existing_session is not None:
        try:
            existing_session.close()
        except (OSError, ConnectionError, RuntimeError) as error:
            log(
                "warning",
                "Error closing existing "
                f"{session_name} session during overwrite: "
                f"{error}",
            )

    log("debug", f"Created {session_name} session.")
    session = chat_downloader_class(**owner.config.as_dict())
    owner.sessions[session_name] = session
    for cookie in owner._cookie_jar:
        session.session.cookies.set_cookie(cookie)

    return session


def close_sessions(owner: ChatDownloaderProto) -> None:
    """Close all active site sessions and clear the cache."""
    for session in owner.sessions.values():
        try:
            session.close()
        except (OSError, ConnectionError, RuntimeError) as error:
            log("warning", f"Error closing session: {error}")
    owner.sessions = {}
