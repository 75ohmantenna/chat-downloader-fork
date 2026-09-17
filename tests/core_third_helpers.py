# SPDX-License-Identifier: MIT

from __future__ import annotations

import io
import logging
from contextlib import contextmanager

import chat_downloader.debugging as dbg


@contextmanager
def captured_logs(logger=None, *, safe=False, formatted=False):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    if safe:
        handler.addFilter(dbg._SafeLogFilter())
    if formatted:
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger = dbg.logger if logger is None else logger
    logger.addHandler(handler)
    try:
        yield stream
    finally:
        logger.removeHandler(handler)


@contextmanager
def restore_loggers():
    mode = dbg.get_testing_mode()
    state = [
        (logger, list(logger.handlers), logger.level, logger.disabled)
        for logger in dbg.loggers
    ]
    try:
        yield
    finally:
        dbg.set_testing_mode(mode)
        for logger, handlers, level, disabled in state:
            logger.handlers = handlers
            logger.setLevel(level)
            logger.disabled = disabled
