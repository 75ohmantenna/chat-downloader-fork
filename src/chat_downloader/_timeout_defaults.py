# SPDX-License-Identifier: MIT

"""Default HTTP timeout constants shared by models and session helpers.

Kept in a separate leaf module so model defaults and session code can share
the same values without creating runtime/session dependencies from
``chat_downloader.models``.
"""

#: TCP connect timeout in seconds (passed as the first element of the
#: ``(connect, read)`` tuple to ``requests``).
from __future__ import annotations

DEFAULT_CONNECT_TIMEOUT: float = 10.0

#: HTTP read timeout in seconds (passed as the second element of the
#: ``(connect, read)`` tuple to ``requests``).
DEFAULT_READ_TIMEOUT: float = 30.0
