# SPDX-License-Identifier: MIT

"""YouTube continuation constants for chat parsing."""

_KNOWN_SEEK_CONTINUATIONS = ["playerSeekContinuationData"]

_KNOWN_CHAT_CONTINUATIONS = [
    "invalidationContinuationData",
    "timedContinuationData",
    "liveChatReplayContinuationData",
    "reloadContinuationData",
]

_KNOWN_CONTINUATIONS = _KNOWN_SEEK_CONTINUATIONS + _KNOWN_CHAT_CONTINUATIONS
