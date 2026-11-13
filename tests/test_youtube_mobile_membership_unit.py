# SPDX-License-Identifier: MIT

"""Captured mobile membership announcements reuse the native membership type."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from chat_downloader import run
from chat_downloader.sites.filters import MessageFilter
from chat_downloader.sites.youtube.constants_message import _MESSAGE_GROUPS
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from chat_downloader.sites.youtube.message_pipeline import process_pipeline_action
from chat_downloader.sites.youtube.parsing.modern_elements import normalize_element
from tests.youtube_third_helpers import http_response, response, video_info

FIXTURE = (
    Path(__file__).parent
    / "fixtures/youtube/live_events/youtube-mobile-new-member.json"
)


def _action():
    return json.loads(FIXTURE.read_text())["continuationContents"][
        "liveChatContinuation"
    ]["actions"][0]


def _item():
    return _action()["addChatItemAction"]["item"]


@pytest.mark.parametrize("replay", [False, True])
def test_mobile_new_member_preserves_identity_badges_header_and_replay_time(replay):
    action = _action()
    if replay:
        action = {
            "replayChatItemAction": {
                "videoOffsetTimeMsec": "12000",
                "actions": [action],
            }
        }
    before = deepcopy(action)
    result = process_pipeline_action(
        action,
        0,
        MessageFilter(_MESSAGE_GROUPS, types_to_add=["membership_item"]),
        None,
    )
    assert result.disposition == "yield"
    message = result.message
    assert message["message_type"] == "membership_item"
    assert message["message_id"] == "mobile-membership-1"
    assert message["author"]["id"] == "UCfixture-member"
    assert message["author"]["name"] == "@FixtureMember"
    assert message["author"]["badges"][0]["title"] == "New member"
    assert message["author"]["badges"][0]["icons"]
    assert message["author"]["images"]
    assert message["header_secondary_text"] == "Welcome to the channel!"
    assert message["message"] is None
    assert "timestamp" not in message
    if replay:
        assert message["time_in_seconds"] == 12
    assert action == before


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("headerRenderer", None),
        ("headerRenderer", []),
        ("headerRenderer.authorName", {}),
        ("headerRenderer.authorName.elementsAttributedString.content", ""),
        ("headerRenderer.authorName.elementsAttributedString.content", []),
        ("headerRenderer.primaryText", None),
        ("headerRenderer.primaryText.elementsAttributedString.content", " "),
        ("headerRenderer.primaryText.elementsAttributedString.content", 1),
    ],
)
def test_malformed_mobile_membership_content_remains_diagnosable(path, value):
    item = _item()
    model = item["elementRenderer"]["newElement"]["type"]["componentType"]["model"][
        "liveChatSponsorshipsNewMemberAnnouncementModel"
    ]
    target = model
    *parents, key = path.split(".")
    for parent in parents:
        target = target[parent]
    target[key] = value
    assert normalize_element(item) is item


def test_mixed_outer_membership_renderers_remain_diagnosable():
    item = _item()
    item["liveChatTextMessageRenderer"] = {"id": "other"}
    assert normalize_element(item) is item


def test_mobile_membership_keeps_valid_badges_among_malformed_optional_entries():
    item = _item()
    model = item["elementRenderer"]["newElement"]["type"]["componentType"]["model"][
        "liveChatSponsorshipsNewMemberAnnouncementModel"
    ]
    badges = model["headerRenderer"]["authorBadges"]
    badges[:0] = [None, "bad", [], {}, {"unrecognizedBadge": {}}]
    before = deepcopy(item)
    normalized = normalize_element(item)["liveChatMembershipItemRenderer"]
    assert len(normalized["authorBadges"]) == 1
    assert item == before


def test_mobile_membership_accepts_absent_optional_images_badges_and_color():
    item = _item()
    model = item["elementRenderer"]["newElement"]["type"]["componentType"]["model"][
        "liveChatSponsorshipsNewMemberAnnouncementModel"
    ]
    model.pop("backgroundColor")
    model["headerRenderer"].pop("authorBadges")
    model["headerRenderer"].pop("authorPhoto")
    normalized = normalize_element(item)["liveChatMembershipItemRenderer"]
    assert normalized["authorName"]
    assert normalized["headerSubtext"]
    assert "authorBadges" not in normalized
    assert "authorPhoto" not in normalized
    assert "backgroundColor" not in normalized


def test_membership_logging_identifier_is_not_message_time():
    item = _item()
    normalized = normalize_element(item)
    item["elementRenderer"]["newElement"]["properties"]["identifierProperties"][
        "uniqueLoggingIdentifier"
    ] = "9999999999999999999"
    assert normalize_element(item) == normalized
    assert "timestampUsec" not in normalized["liveChatMembershipItemRenderer"]


@pytest.mark.parametrize("color", [None, "bad", [], False, 1.5, -1, 2**40])
def test_invalid_optional_membership_colors_are_omitted(color):
    item = _item()
    model = item["elementRenderer"]["newElement"]["type"]["componentType"]["model"][
        "liveChatSponsorshipsNewMemberAnnouncementModel"
    ]
    model["backgroundColor"] = color
    assert (
        "backgroundColor"
        not in normalize_element(item)["liveChatMembershipItemRenderer"]
    )


@pytest.mark.parametrize("color", [0, 0xFFFFFFFF])
def test_membership_argb_color_boundaries_are_preserved(color):
    item = _item()
    model = item["elementRenderer"]["newElement"]["type"]["componentType"]["model"][
        "liveChatSponsorshipsNewMemberAnnouncementModel"
    ]
    model["backgroundColor"] = color
    assert (
        normalize_element(item)["liveChatMembershipItemRenderer"]["backgroundColor"]
        == color
    )


@pytest.mark.parametrize("field", ["icon", "customThumbnail"])
@pytest.mark.parametrize("value", [None, "bad", [], {}])
def test_malformed_optional_badge_fields_do_not_break_membership(field, value):
    item = _item()
    model = item["elementRenderer"]["newElement"]["type"]["componentType"]["model"][
        "liveChatSponsorshipsNewMemberAnnouncementModel"
    ]
    model["headerRenderer"]["authorBadges"][0]["liveChatAuthorBadgeRenderer"][field] = (
        value
    )
    result = process_pipeline_action(
        {"addChatItemAction": {"item": item}},
        0,
        MessageFilter(_MESSAGE_GROUPS, types_to_add=["membership_item"]),
        None,
    )
    assert result.message["message_type"] == "membership_item"
    assert result.message["author"]["name"] == "@FixtureMember"


def test_membership_native_icon_badge_is_preserved():
    item = _item()
    model = item["elementRenderer"]["newElement"]["type"]["componentType"]["model"][
        "liveChatSponsorshipsNewMemberAnnouncementModel"
    ]
    model["headerRenderer"]["authorBadges"] = [
        {
            "liveChatAuthorBadgeRenderer": {
                "icon": {"iconType": "VERIFIED"},
                "tooltip": "Verified",
            }
        }
    ]
    result = normalize_element(item)["liveChatMembershipItemRenderer"]
    assert result["authorBadges"][0]["liveChatAuthorBadgeRenderer"]["icon"] == {
        "iconType": "VERIFIED"
    }


@pytest.mark.parametrize("field", ["authorPhoto", "authorBadges"])
@pytest.mark.parametrize("value", [None, "bad", {}, []])
def test_malformed_optional_membership_metadata_keeps_required_content(field, value):
    item = _item()
    model = item["elementRenderer"]["newElement"]["type"]["componentType"]["model"][
        "liveChatSponsorshipsNewMemberAnnouncementModel"
    ]
    model["headerRenderer"][field] = value
    renderer = normalize_element(item)["liveChatMembershipItemRenderer"]
    assert field not in renderer
    assert renderer["headerSubtext"]


def test_mobile_new_member_matches_native_membership_output():
    action = _action()
    element = action["addChatItemAction"]["item"]["elementRenderer"]
    model = element["newElement"]["type"]["componentType"]["model"][
        "liveChatSponsorshipsNewMemberAnnouncementModel"
    ]
    header = model["headerRenderer"]
    badge = header["authorBadges"][0]["liveChatAuthorBadgeRenderer"]
    native = {
        "addChatItemAction": {
            "item": {
                "liveChatMembershipItemRenderer": {
                    "id": "mobile-membership-1",
                    "authorExternalChannelId": "UCfixture-member",
                    "authorName": {"simpleText": "@FixtureMember"},
                    "headerSubtext": {"runs": [{"text": "Welcome to the channel!"}]},
                    "authorPhoto": {
                        "thumbnails": deepcopy(
                            header["authorPhoto"]["elementsImage"]["sources"]
                        )
                    },
                    "authorBadges": [
                        {
                            "liveChatAuthorBadgeRenderer": {
                                "tooltip": "New member",
                                "customThumbnail": {
                                    "thumbnails": deepcopy(
                                        badge["customThumbnail"]["elementsImage"][
                                            "sources"
                                        ]
                                    )
                                },
                            }
                        }
                    ],
                    "backgroundColor": model["backgroundColor"],
                }
            }
        }
    }
    msg_filter = MessageFilter(_MESSAGE_GROUPS, types_to_add=["membership_item"])
    expected = process_pipeline_action(native, 0, msg_filter, None).message
    assert expected["author"]["name"] == "@FixtureMember"
    assert process_pipeline_action(action, 0, msg_filter, None).message == expected


@pytest.mark.parametrize("status", ["live", "was_live"])
def test_mobile_membership_verifies_through_polling_writers_and_manifest(
    tmp_path, monkeypatch, status
):
    action = _action()
    if status == "was_live":
        action = {
            "replayChatItemAction": {"videoOffsetTimeMsec": "1000", "actions": [action]}
        }
    monkeypatch.setattr(
        YouTubeChatDownloader,
        "_get_initial_video_info",
        lambda *_args, **_kwargs: (
            video_info(status, duration=30),
            {"INNERTUBE_API_KEY": "fixture"},
        ),
    )
    monkeypatch.setattr(
        YouTubeChatDownloader,
        "_session_post",
        lambda *_args, **_kwargs: http_response(payload=response([deepcopy(action)])),
    )
    result = run(
        url="https://www.youtube.com/watch?v=abcdefghijk",
        request_profile="youtube_android",
        chat_type="top",
        message_types=["membership_item"],
        quiet=True,
        output=[str(tmp_path / "chat.jsonl"), str(tmp_path / "chat.txt")],
        verify_output=True,
        run_manifest=str(tmp_path / "run.json"),
    )
    assert result.success
    assert result.parity_status == "passed"
    assert result.provider_inspection["status"] == "ok"
    assert result.message_type_counts == {"membership_item": 1}
    stored = json.loads((tmp_path / "chat.jsonl").read_text())
    assert stored["header_secondary_text"] == "Welcome to the channel!"
    assert "FixtureMember" in (tmp_path / "chat.txt").read_text()
    manifest = json.loads((tmp_path / "run.json").read_text())
    assert manifest["provider_inspection"] == result.provider_inspection
