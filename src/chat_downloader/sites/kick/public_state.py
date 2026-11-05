# SPDX-License-Identifier: MIT

"""Public metadata and viewer snapshots, independent of private push feeds."""

from __future__ import annotations

from contextlib import suppress
from time import monotonic
from typing import TYPE_CHECKING

from requests.exceptions import RequestException

from chat_downloader.debugging import logger
from chat_downloader.utils.json_types import get_dict, get_int, get_list

from .api_client import _classify_response, _decode_json
from .errors import KickError
from .http_session import create_kick_session

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from threading import Event

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
        self._channel: JSONDict = {}
        self._last_metadata: JSONDict | None = None
        self._last_viewers: tuple[JSONList, bool] | None = None

    def _get(
        self, url: str, *, resource: str, params: dict[str, str] | None = None
    ) -> object:
        if self._closed:
            msg = "KickPublicState is closed."
            raise RuntimeError(msg)
        started = monotonic()
        status = "transport_error"
        try:
            response = self._session.get(
                url, params=params, timeout=self._timeout, allow_redirects=False
            )
            status = str(response.status_code)
        finally:
            logger.debug(
                "Kick HTTP resource=%s origin=public status=%s elapsed=%.3fs",
                resource,
                status,
                monotonic() - started,
            )
        _classify_response(response, context="public state", resource="realtime")
        return _decode_json(response, context="public state", resource="realtime")

    def metadata(self, username: str) -> JSONDict:
        """Fetch the public channel snapshot without account credentials."""
        payload = self._get(
            f"https://kick.com/api/v2/channels/{username}", resource="channel"
        )
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
            "https://kick.com/current-viewers",
            resource="viewers",
            params={"ids[]": str(livestream_id)},
        )
        if not isinstance(payload, list):
            msg = "Kick viewer counts were not an array."
            raise TypeError(msg)
        return payload

    def _poll_metadata(
        self, username: str, record_diagnostic: Callable[[str], None]
    ) -> JSONDict | None:
        try:
            channel = self.metadata(username)
        except (RequestException, OSError, KickError, ValueError, TypeError) as error:
            record_diagnostic("public_state_poll_failure_count")
            logger.debug("Kick public metadata poll failed (%s).", type(error).__name__)
            return None
        record_diagnostic("public_state_poll_count")
        return channel

    def _metadata_snapshot(self, channel: JSONDict) -> JSONDict | None:
        metadata: JSONDict = {
            "livestream": channel.get("livestream"),
            "followers_count": channel.get("followers_count"),
            "chatroom": channel.get("chatroom"),
        }
        if metadata == self._last_metadata:
            return None
        self._last_metadata = metadata
        return {
            "event": "kick:public_state",
            "data": metadata,
            "source": "public_rest",
        }

    def _viewer_snapshot(self) -> JSONDict | None:
        viewers = self.viewers(self._channel)
        offline = "livestream" in self._channel and self._channel["livestream"] is None
        viewer_state = (viewers, offline)
        if viewer_state == self._last_viewers:
            return None
        self._last_viewers = viewer_state
        return {
            "event": "kick:viewer_count",
            "data": viewers,
            "source": "public_rest",
            "offline": offline,
        }

    def snapshots(
        self,
        username: str,
        stopped: Event,
        record_diagnostic: Callable[[str], None],
        *,
        initial_channel: JSONDict | None = None,
    ) -> Iterator[JSONDict]:
        """Emit changed observations, seeding the first cycle from setup metadata."""
        channel = (
            initial_channel
            if initial_channel is not None
            else self._poll_metadata(username, record_diagnostic)
        )
        if stopped.is_set():
            return
        category_changed = (
            channel is not None
            and initial_channel is None
            and category_feeds(channel) != category_feeds(self._channel)
        )
        if channel is not None:
            self._channel = channel
            if (metadata := self._metadata_snapshot(channel)) is not None:
                yield metadata
        if stopped.is_set():
            return
        try:
            viewers = self._viewer_snapshot()
            if stopped.is_set():
                return
            if viewers is not None:
                yield viewers
        except (RequestException, OSError, KickError, ValueError, TypeError) as error:
            record_diagnostic("public_state_poll_failure_count")
            logger.debug("Kick public viewer poll failed (%s).", type(error).__name__)
        if category_changed:
            msg = "Kick public category feeds changed."
            raise ConnectionError(msg)

    def close(self) -> None:
        """Close the isolated session on its request-owning thread."""
        if not self._closed:
            self._closed = True
            with suppress(OSError, RuntimeError):
                self._session.close()
