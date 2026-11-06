# SPDX-License-Identifier: MIT

from __future__ import annotations

import io
import sys
from types import SimpleNamespace

import pytest

from chat_downloader.utils import console_utils


def test_preferredencoding_fallback_is_utf8_when_invalid(monkeypatch) -> None:
    monkeypatch.setattr(
        console_utils.locale,
        "getpreferredencoding",
        lambda: "nope-nope",
    )
    assert console_utils.preferredencoding() == "utf-8"


def test_find_next_nonbmp_position() -> None:
    assert console_utils._find_next_nonbmp_position("abc") == 3
    assert (
        console_utils._find_next_nonbmp_position("a\U0001f600b") == 1
    )  # grinning face


def test_safe_print_writes_to_text_stream() -> None:
    out = io.StringIO()
    console_utils.safe_print("a", "b", sep="-", end="!", out=out, flush=True)
    assert out.getvalue() == "a-b!"


@pytest.mark.parametrize(
    ("encoding", "expected"),
    [
        ("ascii", b"A\\xe9\\u6f22\\U0001f600B\n"),
        ("cp1252", b"A\xe9\\u6f22\\U0001f600B\n"),
        ("utf-8", "Aé漢😀B\n".encode()),
    ],
)
@pytest.mark.parametrize("errors", ["strict", "ignore", "replace"])
def test_safe_print_preserves_unencodable_characters(
    encoding, expected, errors
) -> None:
    buffer = io.BytesIO()
    out = io.TextIOWrapper(buffer, encoding=encoding, errors=errors)
    console_utils.safe_print("Aé漢😀B", out=out, flush=True)
    assert buffer.getvalue() == expected


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "iso2022_jp"])
def test_safe_print_preserves_encoder_state_with_other_text_writes(encoding) -> None:
    buffer = io.BytesIO()
    out = io.TextIOWrapper(buffer, encoding=encoding)
    out.write("first\n")
    console_utils.safe_print("日本語", out=out)
    out.write("middle\n")
    console_utils.safe_print("last", out=out, flush=True)
    assert buffer.getvalue().decode(encoding) == "first\n日本語\nmiddle\nlast\n"


def test_safe_print_escapes_before_advancing_a_stateful_encoder() -> None:
    buffer = io.BytesIO()
    out = io.TextIOWrapper(buffer, encoding="iso2022_jp")
    console_utils.safe_print("日本😀", out=out)
    console_utils.safe_print("語", out=out, flush=True)
    assert buffer.getvalue().decode("iso2022_jp") == "日本\\U0001f600\n語\n"


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16"])
def test_safe_print_first_unencodable_record_keeps_stream_signature(encoding) -> None:
    buffer = io.BytesIO()
    out = io.TextIOWrapper(buffer, encoding=encoding)
    console_utils.safe_print("\ud800", out=out, flush=True)
    assert buffer.getvalue() == "\\ud800\n".encode(encoding)


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16"])
def test_safe_print_explicit_encoding_preserves_repeated_records(encoding) -> None:
    buffer = io.BytesIO()
    out = io.TextIOWrapper(buffer, encoding="ascii")
    console_utils.safe_print("one", out=out, encoding=encoding)
    console_utils.safe_print("two", out=out, encoding=encoding, flush=True)
    assert buffer.getvalue().decode(encoding) == "one\ntwo\n"


def test_safe_print_escapes_without_changing_destination_error_policy() -> None:
    buffer = io.BytesIO()
    out = io.TextIOWrapper(buffer, encoding="ascii", errors="strict")
    console_utils.safe_print("猫", out=out, flush=True)
    assert buffer.getvalue() == b"\\u732b\n"
    with pytest.raises(UnicodeEncodeError):
        out.write("é")


def test_safe_print_uses_windows_console_short_circuit(monkeypatch) -> None:
    out = SimpleNamespace(fileno=lambda: 1)
    writes = []

    def fake_windows_write_string(text, current_out) -> bool:
        writes.append((text, current_out))
        return True

    monkeypatch.setattr(console_utils.sys, "platform", "win32")
    monkeypatch.setattr(
        console_utils,
        "_windows_write_string",
        fake_windows_write_string,
    )

    console_utils.safe_print("hello", out=out)

    assert writes == [("hello\n", out)]


def test_pause_calls_input(monkeypatch) -> None:
    prompts = []

    def fake_input(prompt="") -> str:
        prompts.append(prompt)
        return ""

    monkeypatch.setattr("builtins.input", fake_input)

    console_utils.pause("Continue?")

    assert prompts == ["Continue?"]


def _install_fake_ctypes(monkeypatch, handlers) -> None:
    class FakeDWORD:
        def __init__(self, value=0) -> None:
            self.value = value

    fake_wintypes = SimpleNamespace(
        HANDLE=int,
        DWORD=FakeDWORD,
        BOOL=int,
        LPWSTR=str,
        LPVOID=object,
    )

    class FakeCtypes:
        wintypes = fake_wintypes
        windll = SimpleNamespace(kernel32=object())

        @staticmethod
        def WINFUNCTYPE(*_args):
            def factory(spec):
                return handlers[spec[0]]

            return factory

        @staticmethod
        def POINTER(value_type):
            return value_type

        @staticmethod
        def byref(value):
            return value

    monkeypatch.setitem(sys.modules, "ctypes", FakeCtypes)
    monkeypatch.setitem(sys.modules, "ctypes.wintypes", fake_wintypes)


def test_get_windows_console_handle_handles_invalid_streams(
    monkeypatch,
) -> None:
    _install_fake_ctypes(monkeypatch, {"GetStdHandle": lambda value: value + 100})

    assert console_utils._get_windows_console_handle(object()) is None
    assert (
        console_utils._get_windows_console_handle(SimpleNamespace(fileno=lambda: 9))
        is None
    )
    assert (
        console_utils._get_windows_console_handle(SimpleNamespace(fileno=lambda: 1))
        == console_utils.STD_OUTPUT_HANDLE + 100
    )


def test_is_valid_console_checks_handle_type_and_console_mode(
    monkeypatch,
) -> None:
    invalid_handle_value = 12345

    def fake_get_file_type(handle):
        if handle == 10:
            return console_utils.FILE_TYPE_CHAR
        if handle == 20:
            return console_utils.FILE_TYPE_CHAR | console_utils.FILE_TYPE_REMOTE
        return 0

    def fake_get_console_mode(handle, mode) -> int:
        if handle == 10:
            mode.value = 1
            return 1
        return 0

    _install_fake_ctypes(
        monkeypatch,
        {
            "GetFileType": fake_get_file_type,
            "GetConsoleMode": fake_get_console_mode,
        },
    )
    fake_ctypes = sys.modules["ctypes"]
    fake_ctypes.wintypes.DWORD = lambda value=0: SimpleNamespace(
        value=invalid_handle_value if value == -1 else value,
    )

    assert console_utils._is_valid_console(None) is False
    assert console_utils._is_valid_console(invalid_handle_value) is False
    assert console_utils._is_valid_console(30) is False
    assert console_utils._is_valid_console(20) is False
    assert console_utils._is_valid_console(10) is True


def test_write_to_windows_console_handles_bmp_and_nonbmp(monkeypatch) -> None:
    calls = []

    def write_console(handle, text, count, written, _reserved) -> int:
        calls.append((handle, text, count))
        written.value = count
        return 1

    _install_fake_ctypes(monkeypatch, {"WriteConsoleW": write_console})

    assert console_utils._write_to_windows_console(7, "ab\U0001f600c") is True
    assert calls == [
        (7, "ab\U0001f600c", 2),
        (7, "\U0001f600c", 2),
        (7, "c", 1),
    ]


def test_write_to_windows_console_raises_when_skip_errors_disabled(
    monkeypatch,
) -> None:
    _install_fake_ctypes(monkeypatch, {"WriteConsoleW": lambda *_args: 0})

    with pytest.raises(OSError, match="Failed to write string"):
        console_utils._write_to_windows_console(7, "hello", skip_errors=False)


def test_write_to_windows_console_retries_when_skip_errors_enabled(
    monkeypatch,
) -> None:
    calls = []

    def write_console(handle, text, count, written, _reserved) -> int:
        calls.append((handle, text, count))
        if len(calls) == 1:
            return 0
        written.value = count
        return 1

    _install_fake_ctypes(monkeypatch, {"WriteConsoleW": write_console})

    assert console_utils._write_to_windows_console(7, "hello", skip_errors=True) is True
    assert calls == [
        (7, "hello", 5),
        (7, "hello", 5),
    ]


def test_write_to_windows_console_bounds_persistent_skipped_errors(
    monkeypatch,
) -> None:
    calls = []

    def write_console(handle, text, count, _written, _reserved) -> int:
        calls.append((handle, text, count))
        return 0

    _install_fake_ctypes(monkeypatch, {"WriteConsoleW": write_console})

    assert console_utils._write_to_windows_console(7, "hello") is False
    assert calls == [(7, "hello", 5)] * 3


def test_write_to_windows_console_avoids_fallback_after_partial_write(
    monkeypatch,
) -> None:
    calls = []

    def write_console(handle, text, count, written, _reserved) -> int:
        calls.append((handle, text, count))
        if len(calls) == 1:
            written.value = count
            return 1
        return 0

    _install_fake_ctypes(monkeypatch, {"WriteConsoleW": write_console})

    assert console_utils._write_to_windows_console(7, "a\U0001f600") is True
    assert calls == [(7, "a\U0001f600", 1)] + [(7, "\U0001f600", 2)] * 3


def test_write_to_windows_console_raises_on_wrong_nonbmp_written_count(
    monkeypatch,
) -> None:
    def write_console(handle, text, count, written, _reserved) -> int:
        written.value = 1  # wrong: non-BMP needs 2 code units
        return 1

    _install_fake_ctypes(monkeypatch, {"WriteConsoleW": write_console})

    with pytest.raises(RuntimeError, match="Expected 2 code units"):
        console_utils._write_to_windows_console(7, "\U0001f600")


def test_write_to_windows_console_raises_on_zero_bmp_written(
    monkeypatch,
) -> None:
    def write_console(handle, text, count, written, _reserved) -> int:
        written.value = 0  # wrong: BMP write reported nothing written
        return 1

    _install_fake_ctypes(monkeypatch, {"WriteConsoleW": write_console})

    with pytest.raises(RuntimeError, match="zero characters written"):
        console_utils._write_to_windows_console(7, "hello")


def test_windows_write_string_delegates_only_for_valid_console(
    monkeypatch,
) -> None:
    monkeypatch.setattr(console_utils, "_get_windows_console_handle", lambda _out: None)
    assert console_utils._windows_write_string("hello", object()) is False

    monkeypatch.setattr(console_utils, "_get_windows_console_handle", lambda _out: 11)
    monkeypatch.setattr(console_utils, "_is_valid_console", lambda _handle: False)
    assert console_utils._windows_write_string("hello", object()) is False

    monkeypatch.setattr(console_utils, "_is_valid_console", lambda _handle: True)
    monkeypatch.setattr(
        console_utils,
        "_write_to_windows_console",
        lambda handle, text, skip_errors=True: (handle, text, skip_errors),
    )
    assert console_utils._windows_write_string(
        "hello",
        object(),
        skip_errors=False,
    ) == (
        11,
        "hello",
        False,
    )


def test_safe_print_defaults_to_stdout(capsys) -> None:
    console_utils.safe_print("hello", "world", sep="-", end="")

    captured = capsys.readouterr()
    assert captured.out == "hello-world"
