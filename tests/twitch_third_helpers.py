# SPDX-License-Identifier: MIT

"""Small wire-payload builders shared by Twitch test areas."""

from __future__ import annotations

from chat_downloader.models import ChatRequest
from chat_downloader.sites.twitch.constants import MESSAGE_REGEX
from chat_downloader.sites.twitch.parsing.messages import _parse_irc_item


def chat_request(**overrides):
    return ChatRequest(
        **{"max_attempts": 2, "message_groups": ["messages"], **overrides}
    )


def irc_control(command, prefix="tmi.twitch.tv"):
    return f":{prefix} {command}\r\n"


def irc_frame(
    tags="", action="PRIVMSG", text="hello", channel="channel", user="testuser"
):
    defaults = {
        "badge-info": "",
        "badges": "",
        "color": "",
        "display-name": "TestUser",
        "emotes": "",
        "flags": "",
        "id": "message-1",
        "mod": "0",
        "room-id": "999",
        "subscriber": "0",
        "tmi-sent-ts": "1",
        "turbo": "0",
        "user-id": "12345",
        "user-type": "",
    }
    defaults.update(item.split("=", 1) for item in tags.split(";") if item)
    tag_text = ";".join(f"{key}={value}" for key, value in defaults.items())
    sender = f"{user}!{user}@{user}.tmi.twitch.tv" if user else "tmi.twitch.tv"
    suffix = f" :{text}" if text is not None else ""
    return f"@{tag_text} :{sender} {action} #{channel}{suffix}\r\n"


def parse_irc(raw, **kwargs):
    match = MESSAGE_REGEX.search(raw)
    assert match is not None
    return _parse_irc_item(match, **kwargs)


def gql_message(text="hello", **fields):
    return {"fragments": [{"text": text}], **fields}


def gql_comment(message=None, **fields):
    return {
        "id": "msg-1",
        "createdAt": "2024-01-01T00:00:01Z",
        "contentOffsetSeconds": 15,
        "message": gql_message() if message is None else message,
        **fields,
    }


def badge_record(title="Mod", prefix="g", **fields):
    return {
        "title": title,
        **{f"image{size}x": f"{prefix}{size}" for size in (1, 2, 4)},
        "clickAction": None,
        "clickURL": None,
        **fields,
    }
