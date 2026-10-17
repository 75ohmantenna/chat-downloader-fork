# SPDX-License-Identifier: MIT

"""Public metadata and viewer snapshots, independent of private push feeds."""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

from chat_downloader.utils.json_types import get_dict, get_int, get_list

from .api_client import (
    _body_looks_like_challenge,
    _check_status,
    _decode_json,
    _raise_for_challenge,
)
from .http_session import create_kick_session

if TYPE_CHECKING:
    from chat_downloader.utils.json_types import JSONDict, JSONList

    from .http_session import _KickSession


def category_feeds(channel: JSONDict) -> list[str]:
    """Find all current numeric livestream category drop channels."""
    categories = get_list(get_dict(channel, "livestream"), "categories")
    ids = {get_int(item, "id") for item in categories if isinstance(item, dict)}
    return [f"drops_category_{value}" for value in sorted(ids) if value > 0]


class KickPublicState:
    """Own anonymous GETs for information that the website periodically polls."""

    def __init__(
        self,
        *,
        proxy: dict[str, str] | None = None,
        trust_env: bool = True,
        timeout: tuple[float, float] = (10.0, 30.0),
        session: _KickSession | None = None,
    ) -> None:
        """Create an isolated public snapshot session."""
        self._session = session or create_kick_session(proxy=proxy, trust_env=trust_env)
        self._timeout = timeout
        self._closed = False

    def _get(self, url: str, params: dict[str, str] | None = None) -> object:
        if self._closed:
            msg = "KickPublicState is closed."
            raise RuntimeError(msg)
        response = self._session.get(
            url, params=params, timeout=self._timeout, allow_redirects=False
        )
        if response.status_code != 423 and _body_looks_like_challenge(response):
            _raise_for_challenge(response, "public state")
        _check_status(response, context="public state", resource="realtime")
        return _decode_json(response, context="public state", resource="realtime")

    def metadata(self, username: str) -> JSONDict:
        """Fetch the public channel snapshot without account credentials."""
        payload = self._get(f"https://kick.com/api/v2/channels/{username}")
        if not isinstance(payload, dict):
            msg = "Kick public metadata was not an object."
            raise TypeError(msg)
        return payload

    def viewers(self, channel: JSONDict) -> JSONList:
        """Fetch counts only for the current livestream, when present."""
        livestream_id = get_int(get_dict(channel, "livestream"), "id")
        if livestream_id < 1:
            return []
        payload = self._get(
            "https://kick.com/current-viewers", {"ids[]": str(livestream_id)}
        )
        if not isinstance(payload, list):
            msg = "Kick viewer counts were not an array."
            raise TypeError(msg)
        return payload

    def close(self) -> None:
        """Close the isolated snapshot session."""
        if not self._closed:
            self._closed = True
            with suppress(OSError, RuntimeError):
                self._session.close()
