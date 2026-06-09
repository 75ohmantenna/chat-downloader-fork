# SPDX-License-Identifier: MIT

"""Tests for CLI signal-handler installation."""

from __future__ import annotations

import contextlib
import os
import signal
import threading
import time

import pytest

from chat_downloader.cli import _install_cli_signal_handlers


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


def test_sigterm_handler_raises_keyboard_interrupt(
    restore_signal_handlers,
) -> None:
    _install_cli_signal_handlers()
    with pytest.raises(KeyboardInterrupt):
        os.kill(os.getpid(), signal.SIGTERM)
        # Give the signal a moment to be delivered on slower CI.
        for _ in range(50):
            time.sleep(0.01)


def test_sigint_handler_raises_keyboard_interrupt(
    restore_signal_handlers,
) -> None:
    _install_cli_signal_handlers()
    with pytest.raises(KeyboardInterrupt):
        os.kill(os.getpid(), signal.SIGINT)
        for _ in range(50):
            time.sleep(0.01)


def test_second_signal_restores_default(restore_signal_handlers) -> None:
    """After one signal, sending another restores the default handler."""
    _install_cli_signal_handlers()

    # First signal: handler raises KeyboardInterrupt.
    with pytest.raises(KeyboardInterrupt):
        os.kill(os.getpid(), signal.SIGTERM)
        for _ in range(50):
            time.sleep(0.01)

    # Second signal: handler also raises KeyboardInterrupt AND restores
    # SIG_DFL for that signal so a third would terminate the process.
    with pytest.raises(KeyboardInterrupt):
        os.kill(os.getpid(), signal.SIGTERM)
        for _ in range(50):
            time.sleep(0.01)

    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL


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
