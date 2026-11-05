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
        (
            logger,
            list(logger.handlers),
            list(logger.filters),
            logger.level,
            logger.disabled,
            logger.propagate,
        )
        for logger in dbg.loggers
    ]
    handlers = {dbg.handler}
    for logger in dbg.loggers:
        handlers.update(logger.handlers)
    handler_state = [
        (handler, list(handler.filters), handler.level, handler.formatter)
        for handler in handlers
    ]
    try:
        yield
    finally:
        dbg.set_testing_mode(mode)
        for logger, handlers, filters, level, disabled, propagate in state:
            logger.handlers = handlers
            logger.filters = filters
            logger.setLevel(level)
            logger.disabled = disabled
            logger.propagate = propagate
        for handler, filters, level, formatter in handler_state:
            handler.filters = filters
            handler.setLevel(level)
            handler.setFormatter(formatter)
