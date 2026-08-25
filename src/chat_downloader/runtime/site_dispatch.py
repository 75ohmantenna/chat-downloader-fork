# SPDX-License-Identifier: MIT

"""Deep URL-to-configured-chat dispatch module."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast
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
    from chat_downloader.sites.base import BaseChatDownloader
    from chat_downloader.sites.models import Chat


class _SiteSessionOwner(Protocol):
    """Narrow seam required to acquire a provider session."""

    def create_session(
        self,
        chat_downloader_class: type[BaseChatDownloader],
        *,
        overwrite: bool = ...,
    ) -> BaseChatDownloader: ...


def _chat_debug_snapshot(chat: Chat) -> dict[str, Any]:
    """Return a log-safe snapshot of chat metadata."""
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


def _execute_chat_generator(
    site_object: BaseChatDownloader,
    generator_method_name: str,
    match: re.Match[str],
    request: ChatRequest,
    site_name: str,
) -> Chat:
    chat_generator = getattr(site_object, generator_method_name, None)
    if not chat_generator:
        msg = f"{generator_method_name} has not been implemented in {site_name}."
        raise NotImplementedError(msg)
    chat: Chat | None = chat_generator(match, request)
    log(
        "debug",
        f'Match found: "{match}". Running "{generator_method_name}" '
        f'function in "{site_name}".',
    )
    if chat is None:
        msg = f'No valid generator found in {site_name} for URL "{request.url}"'
        raise ChatGeneratorError(msg)
    return chat


def _create_chat_for_site(
    owner: _SiteSessionOwner,
    site: type[BaseChatDownloader],
    match_info: tuple[str, re.Match[str]],
    request: ChatRequest,
) -> Chat:
    generator_method_name, match = match_info
    site_object = owner.create_session(site)
    resolved_request = request.resolved_for_site(site_object)
    log("info", f"Site: {site_object._NAME}")
    log(
        "debug",
        f"Program parameters: {sanitize_for_log(resolved_request.as_dict())}",
    )
    chat = _execute_chat_generator(
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


def _match_site(owner: _SiteSessionOwner, request: ChatRequest) -> Chat | None:
    for site in get_all_sites():
        match_info = site.matches(request.url)
        if match_info:
            return _create_chat_for_site(owner, site, match_info, request)
    return None


def dispatch_chat(owner: _SiteSessionOwner, request: ChatRequest) -> Chat:
    """Resolve one request into a fully configured provider chat."""
    if not request.url:
        msg = "No URL provided."
        raise URLNotProvided(msg)

    effective_request = request
    parsed = urlparse(request.url)
    if request.url.startswith("//"):
        effective_request = request.with_updates(url="https:" + request.url)
    elif not parsed.scheme:
        effective_request = request.with_updates(url="https://" + request.url)

    chat = _match_site(owner, effective_request)
    if chat is not None:
        return chat

    parsed = urlparse(effective_request.url)
    if parsed.netloc:
        msg = f"Site not supported: {parsed.netloc}"
        raise SiteNotSupported(msg)
    msg = f'Invalid URL: "{effective_request.url}"'
    raise InvalidURL(msg)
