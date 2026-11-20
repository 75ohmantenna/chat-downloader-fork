# SPDX-License-Identifier: MIT

"""Tests for CLI signal-handler installation."""

from __future__ import annotations

import contextlib
import signal
import subprocess
import sys
import threading

import pytest

from chat_downloader.cli import _install_cli_signal_handlers, main


@pytest.fixture
def restore_signal_handlers():
    """Snapshot and restore SIGINT/SIGTERM around each test."""
    saved = {
        signal.SIGINT: signal.getsignal(signal.SIGINT),
        signal.SIGTERM: signal.getsignal(signal.SIGTERM),
    }
    yield
    for sig, handler in saved.items():
        with contextlib.suppress(ValueError, OSError):
            signal.signal(sig, handler)


@pytest.fixture(name="install_signal_handler")
def _install_signal_handler(restore_signal_handlers):
    """Install the CLI handlers and return a callable for safe injection."""

    def install(signum):
        previous = signal.getsignal(signum)
        _install_cli_signal_handlers()
        handler = signal.getsignal(signum)
        assert callable(handler), f"No callable installed for {signum.name}"
        assert handler is not previous, f"Handler was not replaced for {signum.name}"

        def inject():
            assert signal.getsignal(signum) is handler
            handler(signum, None)

        return inject

    return install


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_installed_handler_raises_keyboard_interrupt(
    install_signal_handler, signum
) -> None:
    interrupt = install_signal_handler(signum)

    with pytest.raises(KeyboardInterrupt):
        interrupt()


def test_second_signal_restores_default(install_signal_handler) -> None:
    interrupt = install_signal_handler(signal.SIGTERM)

    with pytest.raises(KeyboardInterrupt):
        interrupt()

    with pytest.raises(KeyboardInterrupt):
        interrupt()
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL


def test_sigint_delivery_in_child() -> None:
    script = """
import signal

from chat_downloader.cli import _install_cli_signal_handlers

signal.signal(signal.SIGINT, signal.SIG_DFL)
_install_cli_signal_handlers()
try:
    signal.raise_signal(signal.SIGINT)
except KeyboardInterrupt:
    pass
else:
    raise AssertionError("SIGINT did not raise KeyboardInterrupt")
"""
    result = subprocess.run(  # noqa: S603 - fixed local interpreter and script
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_install_is_noop_off_main_thread(restore_signal_handlers) -> None:
    """Installing from a non-main thread must not crash."""
    errors: list[BaseException] = []

    def target() -> None:
        try:
            _install_cli_signal_handlers()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    assert errors == []


def test_main_restores_handlers_after_version_exit(restore_signal_handlers):
    before = [signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)]
    with pytest.raises(SystemExit) as error:
        main(["--version"])
    assert error.value.code == 0
    assert [signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)] == before
