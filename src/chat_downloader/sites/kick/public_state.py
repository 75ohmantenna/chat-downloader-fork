# SPDX-License-Identifier: MIT

"""Public metadata and viewer snapshots, independent of private push feeds."""

from __future__ import annotations

from contextlib import suppress
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING

from requests.exceptions import RequestException

from chat_downloader.debugging import logger
from chat_downloader.utils.json_types import get_dict, get_int, get_list

from .api_client import _classify_response, _decode_json
from .errors import KickError, KickServerError
from .http_response import fetch_bounded_response
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


class PublicSnapshotRetry:
    """Retain endpoint cooldowns across transports for a single live capture."""

    def __init__(self) -> None:
        """Start without a server-imposed wait."""
        self._lock = Lock()
        self.metadata_at = 0.0
        self.viewers_at = 0.0
        self._cached_channel: JSONDict | None = None

    def cached_channel(self) -> JSONDict | None:
        """Reuse the last observation for setup while its endpoint is throttled."""
        with self._lock:
            return self._cached_channel if monotonic() < self.metadata_at else None

    def defer_metadata(self, retry_at: float, channel: JSONDict) -> None:
        """Remember the minimum wait and a channel snapshot for reconnect setup."""
        with self._lock:
            if retry_at > self.metadata_at:
                self.metadata_at = retry_at
                self._cached_channel = channel

    def defer_viewers(self, retry_at: float) -> None:
        """Honor all minimum delays observed for the viewer endpoint."""
        with self._lock:
            self.viewers_at = max(self.viewers_at, retry_at)


class KickPublicState:
    """Own anonymous GETs for information that the website periodically polls."""

    def __init__(
        self,
        *,
        proxy: dict[str, str] | None = None,
        trust_env: bool = True,
        timeout: tuple[float, float] = (10.0, 30.0),
        session: _KickSession | None = None,
        snapshot_retry: PublicSnapshotRetry | None = None,
    ) -> None:
        """Create an isolated public snapshot session."""
        self._session = session or create_kick_session(proxy=proxy, trust_env=trust_env)
        self._timeout = timeout
        self._closed = False
        self._channel: JSONDict = {}
        self._last_metadata: JSONDict | None = None
        self._last_viewers: tuple[JSONList, bool] | None = None
        self._snapshot_retry = snapshot_retry or PublicSnapshotRetry()

    def next_poll_at(self, cadence: float) -> float:
        """Delay the worker when no endpoint can be polled at its usual cadence."""
        return max(
            cadence,
            min(self._snapshot_retry.metadata_at, self._snapshot_retry.viewers_at),
        )

    @staticmethod
    def _retry_at(error: Exception) -> float:
        if isinstance(error, KickServerError) and error.retry_after is not None:
            return monotonic() + error.retry_after
        return 0.0

    def _get(
        self, url: str, *, resource: str, params: dict[str, str] | None = None
    ) -> object:
        if self._closed:
            msg = "KickPublicState is closed."
            raise RuntimeError(msg)
        started = monotonic()
        status = "transport_error"
        try:
            response, response_exceeded = fetch_bounded_response(
                self._session,
                "GET",
                url,
                {"params": params, "timeout": self._timeout, "allow_redirects": False},
            )
            status = str(response.status_code)
        finally:
            logger.debug(
                "Kick HTTP resource=%s origin=public status=%s elapsed=%.3fs",
                resource,
                status,
                monotonic() - started,
            )
        _classify_response(
            response,
            context="public state",
            resource="realtime",
            response_exceeded=response_exceeded,
        )
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
        if monotonic() < self._snapshot_retry.metadata_at:
            return None
        try:
            channel = self.metadata(username)
        except (RequestException, OSError, KickError, ValueError, TypeError) as error:
            record_diagnostic("public_state_poll_failure_count")
            self._snapshot_retry.defer_metadata(self._retry_at(error), self._channel)
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
        if (viewers := self._poll_viewers(stopped, record_diagnostic)) is not None:
            yield viewers
        if category_changed:
            msg = "Kick public category feeds changed."
            raise ConnectionError(msg)

    def _poll_viewers(
        self, stopped: Event, record_diagnostic: Callable[[str], None]
    ) -> JSONDict | None:
        if (
            monotonic() < self._snapshot_retry.viewers_at
            and get_int(get_dict(self._channel, "livestream"), "id") > 0
        ):
            return None
        try:
            viewers = self._viewer_snapshot()
            return None if stopped.is_set() else viewers
        except (RequestException, OSError, KickError, ValueError, TypeError) as error:
            self._snapshot_retry.defer_viewers(self._retry_at(error))
            record_diagnostic("public_state_poll_failure_count")
            logger.debug("Kick public viewer poll failed (%s).", type(error).__name__)
            return None

    def close(self) -> None:
        """Close the isolated session on its request-owning thread."""
        if not self._closed:
            self._closed = True
            with suppress(OSError, RuntimeError):
                self._session.close()
