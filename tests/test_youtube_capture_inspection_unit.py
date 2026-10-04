# SPDX-License-Identifier: MIT

from __future__ import annotations

import json

import pytest

from chat_downloader.sites.filters import MessageFilter, TimeRangeFilter
from chat_downloader.sites.youtube.capture_inspection import inspect_capture
from chat_downloader.sites.youtube.constants_message import _MESSAGE_GROUPS
from chat_downloader.sites.youtube.message_pipeline import process_pipeline_action
from scripts.inspect_youtube_capture import main


def _write(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def _text(identity="one", offset=0, **extra):
    return {
        "message_type": "text_message",
        "message_id": identity,
        "time_in_seconds": offset,
        **extra,
    }


def _summary(**extra):
    return {"success": True, "message_count": 0, "message_type_counts": {}, **extra}


def test_mobile_timing_is_reported_without_inventing_or_rejecting_timestamps(tmp_path):
    rows = [
        _text("one", 0),
        _text("two", 2),
        _text("three", -1, timestamp=1000),
        {"message_type": "chat_ended"},
    ]
    report = inspect_capture(
        _write(tmp_path / "capture.jsonl", rows),
        run_summary=_summary(
            message_count=4,
            message_type_counts={"text_message": 3, "chat_ended": 1},
            provider_diagnostics={
                "initial_request_profile": "youtube_web",
                "active_request_profile": "youtube_android",
                "chat_view": "Live chat",
                "bootstrap_request_count": 6,
            },
        ),
    )
    assert report["status"] == "ok"
    assert report["missing_source_timestamps"] == 2
    assert report["zero_offset_without_timestamp"] == 1
    assert report["minimum_offset_seconds"] == -1
    assert report["maximum_offset_seconds"] == 2
    assert report["offset_backsteps"] == 1
    assert report["run_accounting"]["active_request_profile"] == "youtube_android"


def test_invalid_rows_are_findings_and_private_content_never_appears(tmp_path, capsys):
    private = "PRIVATE_SENTINEL"
    rows = [
        _text(private, timestamp=True),
        _text(private),
        _text("", offset="bad"),
        {"message_type": private},
        {"message_type": "viewer_engagement_message"},
    ]
    path = _write(tmp_path / "capture.jsonl", rows)
    with path.open("a") as stream:
        stream.write("[]\n{bad PRIVATE_SENTINEL\n")
    assert main([str(path)]) == 1
    output = capsys.readouterr().out
    assert private not in output
    report = json.loads(output)
    assert set(report["issues"]) == {
        "invalid_timestamp",
        "duplicate_text_message_id",
        "missing_text_message_id",
        "invalid_replay_offset",
        "unknown_message_type",
        "non_object_record",
        "invalid_jsonl",
    }


@pytest.mark.parametrize("offset", [True, "1", 10**400])
def test_invalid_offsets_do_not_crash_inspection(tmp_path, offset):
    report = inspect_capture(_write(tmp_path / "capture.jsonl", [_text(offset=offset)]))
    assert report["issues"]["invalid_replay_offset"]["count"] == 1


@pytest.mark.parametrize(
    "summary",
    [
        _summary(message_count=True),
        _summary(message_count=-1),
        _summary(prior_message_count="private"),
        _summary(success="private"),
        _summary(message_type_counts={"private": True}),
        _summary(message_type_counts=[]),
        _summary(provider_diagnostics={"bootstrap_request_count": -1}),
    ],
)
def test_invalid_summary_is_rejected(summary):
    with pytest.raises(ValueError, match="invalid_run_summary"):
        inspect_capture(None, run_summary=summary)


def test_resume_manifest_does_not_compare_current_type_counts_with_prior_records(
    tmp_path,
):
    path = _write(tmp_path / "capture.jsonl", [_text("one"), _text("two")])
    report = inspect_capture(
        path,
        run_summary=_summary(
            message_count=1,
            prior_message_count=1,
            message_type_counts={"text_message": 1},
        ),
    )
    assert report["status"] == "ok"
    assert report["run_accounting"]["type_counts_comparable"] is False
    assert report["run_accounting"]["message_type_count_mismatches"] is None


@pytest.mark.parametrize(
    "summary",
    [
        _summary(message_count=1),
        _summary(message_type_counts={"text_message": 1}),
        _summary(success=False),
        _summary(parity_status="failed"),
        _summary(provider_diagnostics={"parse_error": 1}),
    ],
)
def test_run_accounting_gaps_and_parser_loss_need_review(summary):
    assert inspect_capture(None, run_summary=summary)["status"] == "review"


def test_manifest_provenance_has_a_fixed_safe_vocabulary(tmp_path, capsys):
    capture = _write(tmp_path / "capture.jsonl", [])
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            _summary(
                provider_diagnostics={
                    "chat_view": ["PRIVATE_SENTINEL"],
                    "active_request_profile": "PRIVATE_SENTINEL",
                }
            )
        )
    )
    assert main([str(capture), "--manifest", str(manifest)]) == 0
    assert "PRIVATE_SENTINEL" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "content",
    [
        "[]",
        '{"success":true,"success":false}',
        "PRIVATE_SENTINEL",
        "x" * (1024 * 1024 + 1),
    ],
)
def test_bad_manifest_is_content_free(tmp_path, capsys, content):
    capture = _write(tmp_path / "capture.jsonl", [])
    manifest = tmp_path / "manifest.json"
    manifest.write_text(content)
    assert main([str(capture), "--manifest", str(manifest)]) == 2
    assert json.loads(capsys.readouterr().out) == {"error": "invalid_manifest"}


def test_missing_input_and_usage_errors_are_content_free(tmp_path, capsys):
    assert main([str(tmp_path / "PRIVATE_SENTINEL")]) == 2
    assert "PRIVATE_SENTINEL" not in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        main(["--PRIVATE_SENTINEL"])
    assert error.value.code == 2
    assert "PRIVATE_SENTINEL" not in capsys.readouterr().out


def test_replaced_text_and_paid_tickers_may_reuse_ids(tmp_path):
    rows = [
        _text("one", timestamp=1000),
        _text("one", action_type="replace_chat_item", timestamp=1001),
        {"message_type": "paid_message", "message_id": "paid"},
        {"message_type": "ticker_paid_message_item", "message_id": "paid"},
    ]
    assert inspect_capture(_write(tmp_path / "capture.jsonl", rows))["status"] == "ok"


@pytest.mark.parametrize(
    "filter_kwargs", [{"types_to_add": ["all"]}, {"groups_to_add": ["all"]}]
)
def test_all_types_control_actions_are_known_to_capture_inspector(
    tmp_path, filter_kwargs
):
    actions = [
        {"removeBannerForLiveChatCommand": {"targetActionId": "banner"}},
        {
            "showLiveChatTooltipCommand": {
                "tooltip": {
                    "tooltipRenderer": {
                        "detailsText": {"runs": [{"text": "Chat help"}]}
                    }
                }
            }
        },
    ]
    rows = []
    for action in actions:
        result = process_pipeline_action(
            action,
            0,
            MessageFilter(_MESSAGE_GROUPS, **filter_kwargs),
            TimeRangeFilter(),
        )
        assert result.disposition == "yield"
        rows.append(result.message)
    counts = {"remove_banner": 1, "tooltip": 1}
    report = inspect_capture(
        _write(tmp_path / "capture.jsonl", rows),
        run_summary=_summary(message_count=2, message_type_counts=counts),
    )
    assert report["status"] == "ok"
    assert report["issues"] == {}
    assert report["message_types"] == counts
    assert report["run_accounting"]["message_type_count_mismatches"] == 0
