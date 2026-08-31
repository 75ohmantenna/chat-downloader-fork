# SPDX-License-Identifier: MIT

"""Internal structural protocols for the Twitch site package.

These protocols describe the small, duck-typed seams that Twitch GraphQL
helpers accept (session post callable, GQL download callable, and the response
shape they consume). Keeping them in a dedicated module lets
``graphql_client`` and ``discovery`` share narrow types without creating
import cycles.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from chat_downloader.utils.json_types import JSONAny, JSONList


class _HTTPResponse(Protocol):
    """Minimal shape of an HTTP response returned by a session post/get.

    Attributes are declared as read-only properties so the protocol matches
    ``requests.Response`` (which uses ``@property`` for these fields) as well
    as plain-attribute classes.
    """

    @property
    def status_code(self) -> int: ...

    @property
    def text(self) -> str: ...

    @property
    def url(self) -> str: ...

    def json(self) -> JSONAny: ...

    def raise_for_status(self) -> None: ...


# Loose argument shape: callers wrap ``requests.Session.post`` with their own
# timeout/auth kwargs, so the exact parameter signature varies across the
# Twitch package. The response shape is ``_HTTPResponse``.
_SessionPost = Callable[..., _HTTPResponse]


class _DownloadGQL(Protocol):
    """Callable that posts a persisted-query GraphQL request.

    Returns the JSON list response (one entry per operation).
    """

    def __call__(
        self,
        session_post: _SessionPost,
        ops: JSONList,
        auth_token: str | None = None,
        client_id: str | None = None,
    ) -> JSONList: ...


if TYPE_CHECKING:

    def _legacy_download_gql_shape(
        session_post: _SessionPost,
        ops: JSONList,
        auth_token: str | None = None,
        client_id: str | None = None,
    ) -> JSONList: ...

    _legacy_download_gql_contract: _DownloadGQL = _legacy_download_gql_shape
