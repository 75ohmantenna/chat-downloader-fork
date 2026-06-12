# SPDX-License-Identifier: MIT

from __future__ import annotations

from chat_downloader.utils.filename_utils import sanitize_filename_component

# ---------------------------------------------------------------------------
# sanitize_filename_component
# ---------------------------------------------------------------------------


class TestSanitizeFilenameComponent:
    def test_none_returns_empty_string(self) -> None:
        assert sanitize_filename_component(None) == ""

    def test_windows_hostile_chars_replaced(self) -> None:
        assert (
            sanitize_filename_component('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"
        )

    def test_backslash_replaced(self) -> None:
        assert sanitize_filename_component("a\\b") == "a_b"

    def test_control_characters_replaced(self) -> None:
        assert sanitize_filename_component("abc\x00def") == "abc_def"
        assert sanitize_filename_component("abc\x1fdef") == "abc_def"
        assert sanitize_filename_component("abc\x7fdef") == "abc_def"

    def test_leading_trailing_dots_stripped(self) -> None:
        assert sanitize_filename_component("..foo..") == "foo"
        assert sanitize_filename_component("  foo  ") == "foo"
        assert sanitize_filename_component(". foo .") == "foo"

    def test_all_dots_returns_replace_char(self) -> None:
        assert sanitize_filename_component("...") == "_"

    def test_empty_after_strip_returns_replace_char(self) -> None:
        assert sanitize_filename_component("   ") == "_"

    def test_reserved_windows_name_prefixed(self) -> None:
        assert sanitize_filename_component("CON") == "_CON"
        assert sanitize_filename_component("con") == "_con"
        assert sanitize_filename_component("NUL") == "_NUL"
        assert sanitize_filename_component("PRN") == "_PRN"
        assert sanitize_filename_component("AUX") == "_AUX"
        assert sanitize_filename_component("COM1") == "_COM1"
        assert sanitize_filename_component("LPT9") == "_LPT9"

    def test_reserved_name_with_extension_prefixed(self) -> None:
        assert sanitize_filename_component("NUL.txt") == "_NUL.txt"
        assert sanitize_filename_component("COM1.log") == "_COM1.log"

    def test_non_reserved_name_unchanged(self) -> None:
        assert sanitize_filename_component("CONSOLE") == "CONSOLE"
        assert sanitize_filename_component("NULLIFY") == "NULLIFY"
        assert sanitize_filename_component("COM") == "COM"

    def test_truncation_to_default_200_bytes(self) -> None:
        long = "a" * 300
        result = sanitize_filename_component(long)
        assert len(result.encode("utf-8")) <= 200

    def test_truncation_custom_length(self) -> None:
        long = "a" * 100
        result = sanitize_filename_component(long, max_length=50)
        assert len(result.encode("utf-8")) <= 50

    def test_no_truncation_when_max_length_zero(self) -> None:
        long = "a" * 300
        result = sanitize_filename_component(long, max_length=0)
        assert len(result) == 300

    def test_custom_replace_char(self) -> None:
        assert sanitize_filename_component("a/b", replace_char="-") == "a-b"

    def test_normal_stream_title_unchanged(self) -> None:
        title = "My Stream 2024-01-15 highlights"
        assert sanitize_filename_component(title) == title

    def test_unicode_multibyte_truncated_cleanly(self) -> None:
        # Each kanji is 3 UTF-8 bytes; 70 chars = 210 bytes > 200 limit
        long = "日" * 70
        result = sanitize_filename_component(long)
        encoded = result.encode("utf-8")
        assert len(encoded) <= 200
        # Result must be valid UTF-8 (no partial multi-byte sequences)
        encoded.decode("utf-8")
