# SPDX-License-Identifier: MIT

"""Token redaction and opt-in debug-sample capture."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .debug_sample_utils import describe_debug_sample, slugify_debug_label
from .debugging import logger

REDACTED = "<redacted>"
_DEBUG_SAMPLE_CAPTURE_ENV = "CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES"
_DEBUG_SAMPLE_DIR_ENV = "CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR"
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
_SENSITIVE_LOG_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "cookies",
        "id_token",
        "proxy",
        "proxy-authorization",
        "set-cookie",
        "x-api-key",
        "x-youtube-identity-token",
    },
)
_SENSITIVE_HEADER_NAME_PARTS = frozenset(
    {
        "auth",
        "authorization",
        "credential",
        "credentials",
        "secret",
        "token",
    }
)
_AUTH_HEADER_VALUE_RE = re.compile(r"(?i)^\s*(?:basic|bearer|oauth|sapisidhash)\s+\S+")

# Defense-in-depth: even after key-based redaction, raw token-like strings
# embedded in serialized payload values (e.g. an Authorization header
# concatenated into a log message, or a SAPISIDHASH copied into a string
# body) should be scrubbed before the sample lands on disk.
_TOKEN_REDACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Tokens carried as JSON string values for any of the named keys.
    re.compile(
        r'(?i)("(?:authorization|sapisid(?:hash)?|set-cookie|cookie|'
        r'bearer|token|x-api-key|x-goog-authuser)"\s*:\s*")[^"]+',
    ),
    # Bare "Authorization: Bearer …" / "SAPISIDHASH …" style strings.
    re.compile(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._\-+/=]{8,}",
    ),
    re.compile(r"(?i)(sapisidhash\s+)[A-Za-z0-9_]{16,}"),
    # JWT-shaped payloads: header.payload.signature.
    re.compile(
        r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
    ),
)


def _scrub_token_like_strings(serialized: str) -> str:
    """Redact token-like substrings from a JSON-serialized payload."""
    for pattern in _TOKEN_REDACTION_PATTERNS:
        if pattern.groups:
            serialized = pattern.sub(rf"\1{REDACTED}", serialized)
        else:
            serialized = pattern.sub(REDACTED, serialized)
    return serialized


def _is_sensitive_header(name: object, value: object) -> bool:
    """Return whether a header name or authentication value carries a secret."""
    if isinstance(name, str):
        normalized = name.lower().replace("_", "-")
        parts = frozenset(normalized.split("-"))
        if (
            normalized in _SENSITIVE_LOG_KEYS
            or normalized in {"api-key", "apikey"}
            or normalized.endswith("-api-key")
            or parts & _SENSITIVE_HEADER_NAME_PARTS
        ):
            return True
    return isinstance(value, str) and _AUTH_HEADER_VALUE_RE.match(value) is not None


def sanitize_for_log(value: Any) -> Any:
    """Return a log-safe copy of ``value`` with sensitive fields redacted."""
    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        for key, item in value.items():
            normalized_key = key.lower() if isinstance(key, str) else key
            if normalized_key == "headers" and isinstance(item, dict):
                sanitized[key] = {
                    k: REDACTED if _is_sensitive_header(k, v) else v
                    for k, v in item.items()
                }
            elif (
                isinstance(normalized_key, str)
                and normalized_key in _SENSITIVE_LOG_KEYS
            ):
                sanitized[key] = REDACTED if item is not None else None
            else:
                sanitized[key] = sanitize_for_log(item)
        return sanitized

    if isinstance(value, list):
        return [sanitize_for_log(item) for item in value]

    if isinstance(value, tuple):
        return tuple(sanitize_for_log(item) for item in value)

    return value


def _debug_sample_capture_enabled() -> bool:
    """Return ``True`` when debug sample capture is explicitly enabled."""
    env_value = os.environ.get(_DEBUG_SAMPLE_CAPTURE_ENV, "")
    return (
        logger.isEnabledFor(logging.DEBUG) and env_value.lower() in _TRUTHY_ENV_VALUES
    )


def capture_debug_sample(label: str, payload: Any) -> str | None:
    """Write a sanitized debug payload to a deterministic JSON file.

    This is opt-in and only active when:

    - the logger is in debug mode
    - ``CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES`` is set to a truthy value
    """
    if not _debug_sample_capture_enabled():
        return None

    try:
        sanitized = sanitize_for_log(payload)
        serialized = json.dumps(
            sanitized,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            default=str,
        )
        # Belt-and-braces scrub: tokens embedded in serialized string values
        # bypass the key-based redaction above.
        serialized = _scrub_token_like_strings(serialized)
        # Stable short digest for fixture names; not used for security
        # decisions.
        digest = hashlib.sha1(
            serialized.encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:12]
        default_sample_dir = str(
            Path(tempfile.gettempdir()) / "chat_downloader_debug_samples"
        )
        sample_dir = Path(
            os.environ.get(_DEBUG_SAMPLE_DIR_ENV, default_sample_dir),
        )
        sample_dir.mkdir(parents=True, exist_ok=True)
        path = sample_dir / f"{slugify_debug_label(label)}-{digest}.json"
        if not path.exists():
            path.write_text(serialized + "\n", encoding="utf-8")
        hint = describe_debug_sample(path)
        logger.debug(
            "Captured debug sample: "
            f"path={path} "
            f"suggested_fixture_site={hint.site} "
            f"suggested_fixture_group={hint.group} "
            f"suggested_fixture_name={hint.fixture_name}",
        )
        return str(path)
    except (OSError, TypeError, ValueError) as exc:
        logger.warning(f"Unable to capture debug sample for {label!r}: {exc}")
        return None


__all__ = ["REDACTED", "capture_debug_sample", "sanitize_for_log"]
