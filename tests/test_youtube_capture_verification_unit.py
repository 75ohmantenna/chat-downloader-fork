# SPDX-License-Identifier: MIT

"""Exercise YouTube inspection through real polling, writers, and the runner."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest

from chat_downloader import run
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from tests.youtube_third_helpers import http_response, item_action, response, video_info


@pytest.fixture
def youtube(monkeypatch):
    actions = []

    def initial(_site, _video_id, _params, video_type="video"):
        extra = (
            {"clip_start_time": 0, "clip_end_time": 30} if video_type == "clip" else {}
        )
        return video_info("was_live", duration=30, **extra), {
            "INNERTUBE_API_KEY": "fixture"
        }

    def post(_site, _url, **_kwargs):
        return http_response(payload=response(deepcopy(actions)))

    monkeypatch.setattr(YouTubeChatDownloader, "_get_initial_video_info", initial)
    monkeypatch.setattr(YouTubeChatDownloader, "_session_post", post)
    return actions


def _text(number):
    return {
        "replayChatItemAction": {
            "videoOffsetTimeMsec": str(number * 1000),
            "actions": [
                item_action(
                    "liveChatTextMessageRenderer",
                    {
                        "id": str(number),
                        "message": {"runs": [{"text": f"Record {number}"}]},
                    },
                )
            ],
        }
    }


def _params(tmp_path, **changes):
    return {
        "url": "https://www.youtube.com/watch?v=abcdefghijk",
        "message_groups": ["all"],
        "quiet": True,
        "verify_output": True,
        "output": [str(tmp_path / "chat.jsonl"), str(tmp_path / "chat.txt")],
        "run_manifest": str(tmp_path / "run.json"),
        **changes,
    }


@pytest.mark.parametrize("route", ["watch?v=abcdefghijk", "clip/fixture"])
def test_video_and_clip_verification_include_content_free_inspection(
    tmp_path, youtube, route
):
    youtube.extend(
        [
            _text(1),
            {"removeBannerForLiveChatCommand": {"targetActionId": "private-banner"}},
        ]
    )
    result = run(
        **_params(tmp_path, url=f"https://www.youtube.com/{route}"),
        require_complete=True,
    )
    assert result.success
    assert result.parity_status == "passed"
    report = result.provider_inspection
    assert report["status"] == "ok"
    assert report["records"] == 3
    assert report["message_types"] == {
        "text_message": 1,
        "remove_banner": 1,
        "chat_ended": 1,
    }
    assert report["missing_source_timestamps"] == 2
    assert "private-banner" not in json.dumps(report)
    manifest = json.loads((tmp_path / "run.json").read_text())
    assert manifest["provider_inspection"] == report


def test_parser_loss_fails_verified_capture_even_when_outputs_match(tmp_path, youtube):
    youtube.extend([_text(1), {"unknownAction": {"private": "PRIVATE_SENTINEL"}}])
    result = run(**_params(tmp_path))
    assert not result.success
    assert result.parity_status == "passed"
    assert result.provider_inspection["status"] == "review"
    assert result.provider_inspection["run_accounting"]["counters"]["parse_error"] == 1
    assert "PRIVATE_SENTINEL" not in json.dumps(result.provider_inspection)


def test_empty_filtered_capture_inspects_without_creating_outputs(tmp_path, youtube):
    youtube.append(_text(1))
    result = run(**_params(tmp_path, message_types=["paid_message"]))
    assert result.success
    assert result.provider_inspection["status"] == "ok"
    assert result.provider_inspection["records"] == 0
    assert not (tmp_path / "chat.jsonl").exists()
    assert not (tmp_path / "chat.txt").exists()


def test_unverified_capture_does_not_run_inspection(tmp_path, youtube):
    youtube.append(_text(1))
    result = run(**_params(tmp_path, verify_output=False))
    assert result.success
    assert result.provider_inspection is None


def test_partial_capture_inspection_preserves_retrieval_failure(
    tmp_path, youtube, monkeypatch
):
    calls = 0

    def post(_site, _url, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("primary retrieval failure")
        return http_response(
            payload=response(
                [_text(1)],
                continuations=[
                    {
                        "timedContinuationData": {
                            "continuation": "next",
                            "timeoutMs": 500,
                        }
                    }
                ],
            )
        )

    monkeypatch.setattr(YouTubeChatDownloader, "_session_post", post)
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.polling_sleep", lambda _: None
    )
    result = run(**_params(tmp_path))
    assert not result.success
    assert result.error_message == "primary retrieval failure"
    assert result.parity_status == "not_run"
    assert result.provider_inspection["records"] == 1
    assert result.provider_inspection["run_accounting"]["run_failed"] is True


def test_resume_inspection_reconciles_prior_records(tmp_path, youtube):
    youtube.extend([_text(1), _text(2), _text(3)])
    params = _params(tmp_path, resume=str(tmp_path / "checkpoint.json"))
    assert run(**params, max_messages=2).success
    params["run_manifest"] = str(tmp_path / "resumed.json")
    result = run(**params, require_complete=True)
    assert result.success
    report = result.provider_inspection
    assert report["status"] == "ok"
    assert report["records"] == 3
    assert report["run_accounting"]["summary_minus_records"] == 0
    assert report["run_accounting"]["type_counts_comparable"] is False
    params["run_manifest"] = str(tmp_path / "already-completed.json")
    finished = run(**params, require_complete=True)
    assert finished.success
    assert finished.message_count == 0
    assert finished.provider_inspection["records"] == 3
    assert finished.provider_inspection["run_accounting"]["summary_minus_records"] == 0


def test_resume_inspection_retains_known_prior_parser_loss(tmp_path, youtube):
    youtube.extend([_text(1), {"unknownAction": {}}])
    params = _params(tmp_path, resume=str(tmp_path / "checkpoint.json"))
    assert not run(**params).success
    youtube.pop()
    params["run_manifest"] = str(tmp_path / "resumed.json")
    result = run(**params)
    assert not result.success
    assert result.parity_status == "passed"
    assert result.provider_inspection["run_accounting"]["prior_record_loss"] is True


@pytest.mark.parametrize(
    "route",
    [
        "channel/UC84kgKHP0mid0gB7Yzuu9IA",
        "@ReviewMikeyUSA",
        "user/fixture",
        "c/fixture",
    ],
)
def test_lazy_user_routes_attach_inspection_before_discovery(
    tmp_path, youtube, monkeypatch, route
):
    youtube.append(_text(1))
    monkeypatch.setattr(
        YouTubeChatDownloader,
        "get_user_videos",
        lambda *_args, **_kwargs: iter(
            [{"video_id": "abcdefghijk", "video_type": "LIVE", "title": "Fixture"}]
        ),
    )
    result = run(
        **_params(tmp_path, url=f"https://www.youtube.com/{route}"), max_messages=1
    )
    assert result.success
    assert result.provider_inspection["status"] == "ok"
    assert result.provider_inspection["records"] == 1


@pytest.mark.parametrize("title", ["Fixture", "Fixture {id}"])
def test_lazy_channel_manifest_hashes_resolved_output_names(
    tmp_path, youtube, monkeypatch, title
):
    youtube.append(_text(1))
    monkeypatch.setattr(
        YouTubeChatDownloader,
        "get_user_videos",
        lambda *_args, **_kwargs: iter(
            [{"video_id": "abcdefghijk", "video_type": "LIVE", "title": title}]
        ),
    )
    result = run(
        **_params(
            tmp_path,
            url="https://www.youtube.com/@fixture",
            output=[
                str(tmp_path / "{title}-{id}.jsonl"),
                str(tmp_path / "{title}-{id}.txt"),
            ],
        ),
        max_messages=1,
    )
    assert result.success
    manifest = json.loads((tmp_path / "run.json").read_text())
    for writer in manifest["outputs"]:
        name = writer["file_name"]
        assert "abcdefghijk" in name
        assert (
            writer["sha256"]
            == hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
        )


def test_late_manifest_collision_preserves_capture(tmp_path, youtube, monkeypatch):
    youtube.append(_text(1))
    monkeypatch.setattr(
        YouTubeChatDownloader,
        "get_user_videos",
        lambda *_args, **_kwargs: iter(
            [{"video_id": "abcdefghijk", "video_type": "LIVE", "title": "Fixture"}]
        ),
    )
    result = run(
        **_params(
            tmp_path,
            url="https://www.youtube.com/@fixture",
            output=[str(tmp_path / "{id}.jsonl"), str(tmp_path / "{id}.txt")],
            run_manifest=str(tmp_path / "abcdefghijk.jsonl"),
        ),
        max_messages=1,
    )
    assert not result.success
    assert "Run manifest must be distinct from chat outputs" in result.error_message
    rows = (tmp_path / "abcdefghijk.jsonl").read_text().splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["message_type"] == "text_message"
