# SPDX-License-Identifier: MIT

"""Downloader-owned site-session and cookie lifecycle."""

from __future__ import annotations

from http.cookiejar import MozillaCookieJar
from typing import TYPE_CHECKING, Any, cast

from chat_downloader.debugging import log
from chat_downloader.sites.base import BaseChatDownloader
from chat_downloader.sites.session import CookieSpec, build_cookie

if TYPE_CHECKING:
    from chat_downloader.models import DownloaderConfig


class _SiteSessionPool:
    """Own cached site downloaders and cookies shared between them."""

    def __init__(self, config: DownloaderConfig) -> None:
        self.config = config
        self.sessions: dict[str, BaseChatDownloader] = {}
        self._cookie_jar = MozillaCookieJar()

    def clear_cookies(self) -> None:
        """Clear current cookies and prevent future cookie-file reloads."""
        self._cookie_jar.clear()
        for session in self.sessions.values():
            session.clear_cookies()
        self.config.cookies = None

    def set_cookie(
        self,
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
        """Store a cookie locally and mirror it to current site sessions."""
        cookie_rest = {} if rest is None else rest
        spec = CookieSpec(
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
        self._cookie_jar.set_cookie(build_cookie(spec))
        for session in self.sessions.values():
            session.set_cookie_value(
                domain=domain,
                name=name,
                value=value,
                expire_time=expire_time,
                port=port,
                path=path,
                secure=secure,
                discard=discard,
                rest=cast("dict[str, str]", cookie_rest),
            )

    def get_cookie(self, name: str, default: Any = None) -> Any:
        """Return a local or site-session cookie value."""
        local = {cookie.name: cookie.value for cookie in self._cookie_jar}
        if name in local:
            return local[name]
        for session in self.sessions.values():
            value = session.get_cookie_value(name, default=None)
            if value is not None:
                return value
        return default

    def create(
        self,
        chat_downloader_class: type[BaseChatDownloader],
        *,
        overwrite: bool = False,
    ) -> BaseChatDownloader:
        """Create or reuse a site downloader session."""
        if not issubclass(chat_downloader_class, BaseChatDownloader):
            msg = (  # type: ignore[unreachable]
                "Unable to create session, class must extend BaseChatDownloader. "
                f"Class given: {chat_downloader_class}"
            )
            raise TypeError(msg)
        if chat_downloader_class == BaseChatDownloader:
            msg = "Unable to create session, class may not be BaseChatDownloader."
            raise TypeError(msg)

        session_name = chat_downloader_class.__name__
        existing = self.sessions.get(session_name)
        if existing is not None and type(existing) is not chat_downloader_class:
            msg = (
                f"Session name collision for {session_name}: "
                f"{type(existing).__module__}.{type(existing).__qualname__} is already "
                "cached."
            )
            raise TypeError(msg)
        if existing is not None and existing._session_closed:
            overwrite = True
        if existing is not None and not overwrite:
            log("debug", f"Reusing existing {session_name} session.")
            return existing

        if existing is not None:
            try:
                existing.close()
            except (OSError, ConnectionError, RuntimeError) as error:
                log(
                    "warning",
                    f"Error closing existing {session_name} session during "
                    f"overwrite: {error}",
                )

        log("debug", f"Created {session_name} session.")
        session = chat_downloader_class(**self.config.as_dict())
        self.sessions[session_name] = session
        for cookie in self._cookie_jar:
            session.session.cookies.set_cookie(cookie)
        return session

    def get(
        self, chat_downloader_class: type[BaseChatDownloader]
    ) -> BaseChatDownloader | None:
        existing = self.sessions.get(chat_downloader_class.__name__)
        if existing is None or type(existing) is not chat_downloader_class:
            return None
        return existing

    def close(self) -> None:
        """Close every cached site downloader and empty the pool."""
        for session in self.sessions.values():
            try:
                session.close()
            except (OSError, ConnectionError, RuntimeError) as error:
                log("warning", f"Error closing session: {error}")
        self.sessions.clear()
