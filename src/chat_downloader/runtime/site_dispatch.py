# SPDX-License-Identifier: MIT

"""Site resolution and chat generator dispatch helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

from chat_downloader.debugging import log
from chat_downloader.errors import (
    ChatGeneratorError,
    InvalidURL,
    SiteNotSupported,
    URLNotProvided,
)
from chat_downloader.redaction import sanitize_for_log
from chat_downloader.sites import get_all_sites

from .chat_pipeline import configure_chat

if TYPE_CHECKING:
    import re

    from chat_downloader.models import ChatRequest
    from chat_downloader.runtime._protocols import ChatDownloaderProto
    from chat_downloader.sites.base import BaseChatDownloader
    from chat_downloader.sites.models import Chat


def validate_url(url: str) -> None:
    """Validate that a URL is provided."""
    if not url:
        msg = "No URL provided."
        raise URLNotProvided(msg)


def resolve_site_defaults(
    request: ChatRequest, site_object: BaseChatDownloader
) -> ChatRequest:
    """Resolve ``SiteDefault`` parameters to site-specific values."""
    return request.resolved_for_site(site_object)


def _chat_debug_snapshot(chat: Chat) -> dict[str, Any]:
    """Return a log-safe snapshot of chat metadata.

    Avoid logging ``chat.__dict__`` directly because it includes the attached
    site session object and other internal state that may carry cookies or
    request headers.
    """
    snapshot: dict[str, Any] = {
        "title": chat.title,
        "duration": chat.duration,
        "status": chat.status,
        "video_type": chat.video_type,
        "start_time": chat.start_time,
        "id": chat.id,
        "site_name": getattr(getattr(chat, "site", None), "_NAME", None),
    }

    output_dispatcher = getattr(chat, "_output_dispatcher", None)
    if output_dispatcher is not None:
        snapshot["writer_count"] = len(getattr(output_dispatcher, "writers", ()))
        snapshot["callback_count"] = len(getattr(output_dispatcher, "callbacks", ()))

    return cast("dict[str, Any]", sanitize_for_log(snapshot))


def execute_chat_generator(
    site_object: BaseChatDownloader,
    generator_method_name: str,
    match: re.Match[str],
    request: ChatRequest,
    site_name: str,
) -> Chat:
    """Execute the site-specific chat generator method."""
    chat_generator = getattr(site_object, generator_method_name, None)
    if not chat_generator:
        msg = f"{generator_method_name} has not been implemented in {site_name}."
        raise NotImplementedError(
            msg,
        )
    chat: Chat | None = chat_generator(match, request)

    log(
        "debug",
        f'Match found: "{match}". '
        f'Running "{generator_method_name}" function '
        f'in "{site_name}".',
    )

    if chat is None:
        msg = f'No valid generator found in {site_name} for url "{request.url}"'
        raise ChatGeneratorError(
            msg,
        )

    return chat


def create_chat_for_site(
    owner: ChatDownloaderProto,
    site: type,
    match_info: tuple[str, re.Match[str]],
    request: ChatRequest,
) -> Chat:
    """Create and configure a chat object for a matched site."""
    generator_method_name, match = match_info

    site_object = owner.create_session(site)
    resolved_request = resolve_site_defaults(request, site_object)

    log("info", f"Site: {site_object._NAME}")
    log(
        "debug",
        f"Program parameters: {sanitize_for_log(resolved_request.as_dict())}",
    )

    chat = execute_chat_generator(
        site_object,
        generator_method_name,
        match,
        resolved_request,
        site.__name__,
    )
    configure_chat(chat, resolved_request, site_object)

    log("debug", f"Chat information: {_chat_debug_snapshot(chat)}")
    log("info", f'Retrieving chat for "{chat.title}".')

    return chat


def try_create_chat_from_sites(
    owner: ChatDownloaderProto,
    url: str,
    request: ChatRequest,
) -> Chat | None:
    """Try to match the URL against all registered sites."""
    for site in get_all_sites():
        match_info = site.matches(url)
        if match_info:
            return create_chat_for_site(owner, site, match_info, request)
    return None


def handle_unsupported_url(
    owner: ChatDownloaderProto, url: str, request: ChatRequest
) -> Chat:
    """Handle a URL that did not match any registered site."""
    parsed = urlparse(url)
    log("debug", str(parsed))

    if parsed.netloc:
        msg = f"Site not supported: {parsed.netloc}"
        raise SiteNotSupported(msg)

    if not parsed.scheme:
        updated_request = request.with_updates(url="https://" + url)
        return owner.get_chat_request(updated_request)

    msg = f'Invalid URL: "{url}"'
    raise InvalidURL(msg)
