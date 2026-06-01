# SPDX-License-Identifier: MIT

"""Default HTTP timeout constants shared by models and session helpers.

Kept in a separate leaf module to avoid circular imports:
``models.py`` imports from ``sites.models``, which triggers ``sites.base``,
which imports ``session.py`` — so neither ``session.py`` nor ``models.py`` can
import the other at module level.  Both can safely import this module because
it has no project-level dependencies.
"""

#: TCP connect timeout in seconds (passed as the first element of the
#: ``(connect, read)`` tuple to ``requests``).
DEFAULT_CONNECT_TIMEOUT: float = 10.0

#: HTTP read timeout in seconds (passed as the second element of the
#: ``(connect, read)`` tuple to ``requests``).
DEFAULT_READ_TIMEOUT: float = 30.0
