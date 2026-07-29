"""Platform registry and DownloaderFacade (compatible entry point)."""

from __future__ import annotations

from typing import Any

from platforms.base import BaseMediaPlatform, MediaInput, Platform
from platforms.bilibili import BilibiliPlatform
from platforms.tiktok import TikTokPlatform
from platforms.youtube import YouTubePlatform

_PLATFORMS: tuple[BaseMediaPlatform, ...] = (
    YouTubePlatform(),
    BilibiliPlatform(),
    TikTokPlatform(),
)

PLATFORM_BY_KEY: dict[str, BaseMediaPlatform] = {p.key.value: p for p in _PLATFORMS}


def get_platform(key: str | Platform | None) -> BaseMediaPlatform | None:
    if key is None:
        return None
    if isinstance(key, Platform):
        key = key.value
    return PLATFORM_BY_KEY.get(key)


def platform_cookie_domains() -> dict[str, tuple[str, ...]]:
    return {p.key.value: p.cookie_domains for p in _PLATFORMS}


def platform_labels() -> dict[str, str]:
    labels = {p.key.value: p.key.label for p in _PLATFORMS}
    labels["generic"] = Platform.GENERIC.label
    return labels


def platform_cookie_config_names() -> dict[str, str]:
    return {p.key.value: p.cookie_config_name for p in _PLATFORMS}


def __getattr__(name: str) -> Any:
    if name == "DownloaderFacade":
        from downloader import YoutubeDownloader

        return YoutubeDownloader
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DownloaderFacade",
    "MediaInput",
    "Platform",
    "PLATFORM_BY_KEY",
    "get_platform",
    "platform_cookie_config_names",
    "platform_cookie_domains",
    "platform_labels",
]
