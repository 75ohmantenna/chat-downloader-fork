# SPDX-License-Identifier: MIT

"""YouTube bootstrap counters and capture publication."""

from __future__ import annotations

from typing import TYPE_CHECKING

from requests.exceptions import RequestException

from chat_downloader.sites.base import CaptureDiagnostics, get_request_diagnostics
from chat_downloader.utils.json_types import get_int, get_str

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, MutableMapping

    import requests

    from chat_downloader.models import ChatRequest


_BOOTSTRAP_COUNTERS = (
    "bootstrap_request_count",
    "bootstrap_http_error_count",
    "bootstrap_network_error_count",
    "bootstrap_fallback_count",
    "bootstrap_profile_switch_count",
)


def merge_bootstrap_diagnostics(
    target: MutableMapping[str, object], source: Mapping[str, object]
) -> None:
    """Accumulate fixed bootstrap counters without copying credentials."""
    for key in _BOOTSTRAP_COUNTERS:
        target[key] = get_int(target, key) + get_int(source, key)


class BootstrapRequests:
    """Count actual bootstrap HTTP calls across retries without retaining bodies."""

    def __init__(self, capture: CaptureDiagnostics | None = None) -> None:
        """Start a local snapshot and initialize the shared counters."""
        self.diagnostics: dict[str, object] = dict.fromkeys(_BOOTSTRAP_COUNTERS, 0)
        self._capture = capture
        if capture is not None:
            for key in _BOOTSTRAP_COUNTERS:
                capture.data.setdefault(key, 0)

    @classmethod
    def for_request(
        cls, site: object, request: ChatRequest | None
    ) -> BootstrapRequests:
        """Use the capture ledger, or metadata-only counters outside a capture."""
        capture = get_request_diagnostics(site, request)
        if (
            capture is None
            and (setup := getattr(site, "_request_diagnostics", None)) is not None
        ):
            capture = CaptureDiagnostics(setup)
        return cls(capture)

    def increment(self, key: str) -> None:
        """Retain a counter locally and in the active retrieval's failure ledger."""
        self.diagnostics[key] = get_int(self.diagnostics, key) + 1
        if (capture := self._capture) is not None:
            capture.data[key] = get_int(capture.data, key) + 1
            capture.publish()

    def profile(self, name: str) -> None:
        """Keep initial and active profiles even when bootstrap raises."""
        self.diagnostics.setdefault("initial_request_profile", name)
        self.diagnostics["active_request_profile"] = name
        if (capture := self._capture) is not None:
            capture.data.setdefault("initial_request_profile", name)
            capture.data["active_request_profile"] = name
            capture.publish()

    def request(
        self,
        method: Callable[..., requests.Response],
        url: str,
        **kwargs: object,
    ) -> requests.Response:
        """Count each transport attempt before it can block."""
        self.increment("bootstrap_request_count")
        try:
            response = method(url, **kwargs)
        except (RequestException, OSError):
            self.increment("bootstrap_network_error_count")
            raise
        if getattr(response, "status_code", 200) >= 400:
            self.increment("bootstrap_http_error_count")
        return response


def publish_bootstrap_snapshot(
    capture: CaptureDiagnostics,
    before: Mapping[str, object],
    snapshot: Mapping[str, object],
) -> None:
    """Use a returned snapshot to fill counts absent from eager accounting."""
    if not snapshot:
        return
    data = capture.data
    for key in _BOOTSTRAP_COUNTERS:
        data[key] = max(
            get_int(data, key), get_int(before, key) + get_int(snapshot, key)
        )
    initial_profile = get_str(snapshot, "initial_request_profile")
    active_profile = get_str(snapshot, "active_request_profile")
    if initial_profile:
        data.setdefault("initial_request_profile", initial_profile)
    if active_profile:
        data["active_request_profile"] = active_profile
    capture.publish()
