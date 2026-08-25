# SPDX-License-Identifier: MIT

"""Token redaction and opt-in debug-sample capture."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
import tempfile
from contextlib import suppress
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote_plus, urlencode, urlsplit, urlunsplit

from .debug_sample_utils import describe_debug_sample, slugify_debug_label

REDACTED = "<redacted>"
_DEBUG_SAMPLE_CAPTURE_ENV = "CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES"
_DEBUG_SAMPLE_DIR_ENV = "CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR"
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
_SENSITIVE_KEY_COMPONENTS = frozenset(
    {
        "auth",
        "authentication",
        "authorization",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "password",
        "passwords",
        "proxy",
        "secret",
        "token",
    }
)
_COMPACT_SENSITIVE_KEYS = frozenset({"apikey", "authuser", "visitordata"})
_KEY_COMPONENT_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|[0-9]+")
_AUTH_HEADER_VALUE_RE = re.compile(r"(?i)^\s*(?:basic|bearer|oauth|sapisidhash)\s+\S+")
_URL_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>]+")
_APPARENT_USERINFO_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+:[^@\s/'\"<>]+@"
)
_QUERY_PAIR_RE = re.compile(r"([?&])([^=\s&#]+)(=)([^&#\s'\"<>]*)")
_LABELED_VALUE_RE = re.compile(
    r"(?i)\b([A-Za-z][A-Za-z0-9_-]*)\s*([:=])\s*"
    r"((?:bearer\s+)?[^\s,;]+)"
)
_QUOTED_FIELD_RE = re.compile(
    r"""(?ix)
    (?P<prefix>
        (?P<key_quote>["'])
        (?P<key>[A-Za-z][A-Za-z0-9_-]*)
        (?P=key_quote)
        \s*:\s*
    )
    (?P<value_quote>["'])
    (?:\\.|(?!(?P=value_quote)).)*
    (?P=value_quote)
    """,
)
_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x1f\x7f]")

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


def _key_components(key: str) -> tuple[str, ...]:
    """Split a key on case transitions and non-alphanumeric separators."""
    return tuple(part.lower() for part in _KEY_COMPONENT_RE.findall(key))


def _is_sensitive_key(key: str) -> bool:
    """Return whether a structured, header, or query key carries a secret."""
    components = _key_components(key)
    if components == ("visitor",) or set(components) & _SENSITIVE_KEY_COMPONENTS:
        return True
    compact = "".join(components)
    if compact in _COMPACT_SENSITIVE_KEYS:
        return True
    pairs = set(pairwise(components))
    return bool(pairs & {("api", "key"), ("visitor", "data"), ("visitor", "id")})


def _redact_query_pair(match: re.Match[str]) -> str:
    """Redact a query-pair value when its decoded key is sensitive."""
    if not _is_sensitive_key(unquote_plus(match.group(2))):
        return match.group()
    return f"{match.group(1)}{match.group(2)}={REDACTED}"


def _redact_malformed_url(url: str) -> str:
    """Conservatively redact a URL that cannot be parsed structurally."""
    url = _APPARENT_USERINFO_RE.sub(f"{REDACTED}@", url)
    return _QUERY_PAIR_RE.sub(_redact_query_pair, url)


def _redact_url(match: re.Match[str]) -> str:
    """Redact URL credentials and sensitive query values in a log message."""
    url = match.group()
    try:
        parsed = urlsplit(url)
        netloc = parsed.netloc.rsplit("@", 1)[-1]
        if "@" in parsed.netloc:
            netloc = f"{REDACTED}@{netloc}"
        query = urlencode(
            [
                (key, REDACTED if _is_sensitive_key(key) else value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            ],
            doseq=True,
        )
        return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))
    except ValueError:
        return _redact_malformed_url(url)


def _escape_control_character(match: re.Match[str]) -> str:
    """Render a terminal control character visibly rather than emitting it."""
    character = match.group()
    names = {"\n": r"\n", "\r": r"\r", "\t": r"\t"}
    return names.get(character, f"\\x{ord(character):02x}")


def _redact_labeled_values(value: str) -> str:
    """Redact sensitive scalars even when a non-sensitive label precedes them."""
    offset = 0
    while match := _LABELED_VALUE_RE.search(value, offset):
        if not _is_sensitive_key(match.group(1)):
            offset = match.start() + 1
            continue
        replacement = f"{match.group(1)}{match.group(2)}{REDACTED}"
        value = f"{value[: match.start()]}{replacement}{value[match.end() :]}"
        offset = match.start() + len(replacement)
    return value


def _redact_quoted_field(match: re.Match[str]) -> str:
    """Redact a sensitive value in serialized JSON or Python mapping syntax."""
    if not _is_sensitive_key(match.group("key")):
        return match.group()
    quote = match.group("value_quote")
    return f"{match.group('prefix')}{quote}{REDACTED}{quote}"


def _sanitize_secret_string(value: str) -> str:
    """Redact secrets without altering non-sensitive payload characters."""
    value = _QUOTED_FIELD_RE.sub(_redact_quoted_field, value)
    value = _URL_RE.sub(_redact_url, value)
    value = _APPARENT_USERINFO_RE.sub(f"{REDACTED}@", value)
    return _redact_labeled_values(value)


def _is_sensitive_header(name: object, value: object) -> bool:
    """Return whether a header name or authentication value carries a secret."""
    return (isinstance(name, str) and _is_sensitive_key(name)) or (
        isinstance(value, str) and _AUTH_HEADER_VALUE_RE.match(value) is not None
    )


def sanitize_for_log(value: Any) -> Any:
    """Return a log-safe copy of ``value`` with sensitive fields redacted."""
    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        for key, item in value.items():
            if (
                isinstance(key, str)
                and key.lower() == "headers"
                and isinstance(item, dict)
            ):
                sanitized[key] = {
                    k: REDACTED if _is_sensitive_header(k, v) else sanitize_for_log(v)
                    for k, v in item.items()
                }
            elif isinstance(key, str) and _is_sensitive_key(key):
                sanitized[key] = REDACTED if item is not None else None
            else:
                sanitized[key] = sanitize_for_log(item)
        return sanitized

    if isinstance(value, list):
        return [sanitize_for_log(item) for item in value]

    if isinstance(value, tuple):
        return tuple(sanitize_for_log(item) for item in value)

    if isinstance(value, str):
        return _sanitize_secret_string(value)

    return value


def render_for_log(value: object) -> str:
    """Redact secrets, then render terminal control characters visibly."""
    rendered = str(sanitize_for_log(value))
    return _CONTROL_CHARACTER_RE.sub(_escape_control_character, rendered)


def _get_logger() -> logging.Logger:
    """Import the configured logger lazily to avoid a module import cycle."""
    from .debugging import logger

    return logger


def _debug_sample_capture_enabled() -> bool:
    """Return ``True`` when debug sample capture is explicitly enabled."""
    env_value = os.environ.get(_DEBUG_SAMPLE_CAPTURE_ENV, "")
    return (
        _get_logger().isEnabledFor(logging.DEBUG)
        and env_value.lower() in _TRUTHY_ENV_VALUES
    )


def _is_private_sample_entry(
    entry: os.stat_result,
    *,
    directory: bool,
) -> bool:
    """Return whether a sample entry has the expected kind, owner, and mode."""
    expected_kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_kind(entry.st_mode):
        return False
    if os.name != "nt":
        if hasattr(os, "getuid") and entry.st_uid != os.getuid():
            return False
        expected_mode = 0o700 if directory else 0o600
        if stat.S_IMODE(entry.st_mode) != expected_mode:
            return False
    return True


def _require_private_sample_entry(
    entry: os.stat_result,
    path: Path,
    *,
    directory: bool,
) -> None:
    """Reject sample entries that could expose or redirect captured data."""
    if not _is_private_sample_entry(entry, directory=directory):
        kind = "directory" if directory else "file"
        message = f"Unsafe debug sample {kind}: {path}"
        raise OSError(message)


def _prepare_sample_directory(sample_dir: Path) -> int:
    """Create and validate the sample directory, returning a secure POSIX fd."""
    sample_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    _require_private_sample_entry(sample_dir.lstat(), sample_dir, directory=True)

    secure_dir_fd = (
        os.name != "nt"
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and os.open in os.supports_dir_fd
    )
    if not secure_dir_fd:
        message = "Secure debug sample creation is unavailable on this platform"
        raise OSError(message)

    descriptor = os.open(
        sample_dir,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        _require_private_sample_entry(
            os.fstat(descriptor),
            sample_dir,
            directory=True,
        )
    except OSError:
        os.close(descriptor)
        raise
    return descriptor


def _existing_sample_stat(path: Path, directory_fd: int) -> os.stat_result:
    """Inspect an existing sample without following its final path component."""
    return os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)


def _unlink_created_sample(path: Path, directory_fd: int) -> None:
    """Best-effort removal of a sample created by the current capture call."""
    with suppress(OSError):
        os.unlink(path.name, dir_fd=directory_fd)


def _write_or_validate_sample(
    path: Path,
    serialized: str,
    directory_fd: int,
) -> None:
    """Create a private sample atomically or validate the existing sample."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        descriptor = os.open(path.name, flags, 0o600, dir_fd=directory_fd)
    except FileExistsError:
        _require_private_sample_entry(
            _existing_sample_stat(path, directory_fd),
            path,
            directory=False,
        )
        return

    try:
        _require_private_sample_entry(os.fstat(descriptor), path, directory=False)
        with os.fdopen(descriptor, "w", encoding="utf-8") as sample_file:
            descriptor = -1
            sample_file.write(serialized + "\n")
    except BaseException:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
        _unlink_created_sample(path, directory_fd)
        raise


def capture_debug_sample(label: str, payload: Any) -> str | None:
    """Write a sanitized debug payload to a deterministic JSON file.

    This is opt-in and only active when:

    - the logger is in debug mode
    - ``CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES`` is set to a truthy value
    """
    if not _debug_sample_capture_enabled():
        return None

    logger = _get_logger()
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
        directory_fd = _prepare_sample_directory(sample_dir)
        try:
            path = sample_dir / f"{slugify_debug_label(label)}-{digest}.json"
            _write_or_validate_sample(path, serialized, directory_fd)
        finally:
            os.close(directory_fd)
        hint = describe_debug_sample(path)
        logger.debug(
            "Captured debug sample: path=%s suggested_fixture_site=%s "
            "suggested_fixture_group=%s suggested_fixture_name=%s",
            path,
            hint.site,
            hint.group,
            hint.fixture_name,
        )
        return str(path)
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Unable to capture debug sample for %r: %s", label, exc)
        return None


__all__ = ["REDACTED", "capture_debug_sample", "render_for_log", "sanitize_for_log"]
