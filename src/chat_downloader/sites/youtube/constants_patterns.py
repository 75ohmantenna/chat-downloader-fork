# SPDX-License-Identifier: MIT

"""YouTube URLs and regex patterns used by the extractor."""

# URL regex patterns for initial data extraction
from __future__ import annotations

_YT_INITIAL_BOUNDARY_RE = r"\s*(?:var\s+(?:meta|head)|</script|\n)"
_YT_INITIAL_DATA_RE = (
    r'(?:window\s*\[\s*["\']ytInitialData["\']\s*\]|ytInitialData)\s*=\s*'
    r"({.+?})\s*;" + _YT_INITIAL_BOUNDARY_RE
)
_YT_INITIAL_PLAYER_RESPONSE_RE = (
    r"ytInitialPlayerResponse\s*=\s*({.+?})\s*;" + _YT_INITIAL_BOUNDARY_RE
)
_YT_CFG_RE = r"ytcfg\.set\s*\(\s*({.+?})\s*\)\s*;"

# URL base
_YT_HOME = "https://www.youtube.com"
_YT_LIVE_CHAT_URL = _YT_HOME + "/live_chat"
_YT_LIVE_CHAT_REPLAY_URL = _YT_HOME + "/live_chat_replay"
_YT_REDIRECT_PATH = "/redirect"

# Auth constants
_YT_DOMAIN = ".youtube.com"
_YT_SOCS_CONSENTED_PREFIX = "CAA"
_YT_SOCS_INIT_VALUE = "CAI"
_YT_SAPISID_EXPIRE_SECONDS = 3_600

# Poll-loop bounds
_YT_MAX_NO_PROGRESS_POLLS = 5
_YT_MAX_PROFILE_FALLBACKS = 3

# URL matching patterns
_YT_RESERVED_USER_PATHS = (
    "account",
    "clip",
    "dashboard",
    "e",
    "embed",
    "feed",
    "gaming",
    "live",
    "live_chat",
    "live_chat_replay",
    "logout",
    "playlist",
    "premium",
    "results",
    "shorts",
    "signin",
    "upload",
    "v",
    "watch",
)

_VALID_URLS = {
    "_get_chat_by_video_id": r"""(?x)^
                 (
                     # http(s):// or protocol-independent URL
                     (?:https?://|//)
                     (?:(?:(?:(?:\w+\.)?[yY][oO][uU][tT][uU][bB][eE](?:-nocookie|kids)?\.com/|
                        youtube\.googleapis\.com/)  # various hostnames
                     (?:.*?\#/)?  # handle anchor (#/) redirect urls
                     (?:  # the various things that can precede the ID:
                         # v/ embed/ e/ shorts/ live/ or watch/
                         (?:(?:v|embed|e|shorts|live|watch)/(?!videoseries))
                         |(?:  # or the v= param in all its forms
                             # preceding watch(_popup|.php) or nothing
                             (?:(?:watch|movie)(?:_popup)?(?:\.php)?/?)?
                             (?:\?|\#!?)  # the params delimiter ? or # or #!
                             # any other preceding param
                             (?:.*?[&;])??
                             v=
                         )
                     ))
                     |(?:
                        youtu\.be  # just youtu.be/xxxx
                     )/)
                 )?  # all until now is optional -> pass naked ID
                 # here is it! the YouTube video ID
                 (?P<id>[0-9A-Za-z_-]{11})""",
    "_get_chat_by_clip_id": r"""(?x)
            (?:https?://|//)
                (?:\w+\.)?
                (?:
                    youtube?\.com
                )/clip/
                (?P<id>[a-zA-Z0-9_-]+)""",
    # while this does match 'watch' urls, it will never
    # return this since the above regex is run before this
    "_get_chat_by_user": r"""(?x)
            (?:https?://|//)
                (?:\w+\.)?
                (?:
                    youtube(?:kids)?\.com
                )/
                (?!(?:"""
    + "|".join(_YT_RESERVED_USER_PATHS)
    + r""")(?:[/?#]|$))
                (?:
                    (?P<type>channel/|c/|user/|@)
                )?
                (?P<id>[a-zA-Z0-9_-]+)""",
}

# Live playlist used for discovery tests
_LIVE_PLAYLIST_URL = _YT_HOME + "/channel/UC4R8DWoMoI7CAwX8_LjQHig"

_VIDEO_TYPE_REMAPPING = {
    # Name : url component
    "videos": "videos",
    "shorts": "shorts",
    "live": "streams",
}

# Consent ID regex
_CONSENT_ID_REGEX = r"PENDING\+(\d+)"
