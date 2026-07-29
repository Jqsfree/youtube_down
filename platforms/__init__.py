"""Multi-platform download plugins + facade."""

from platforms.base import MediaInput, Platform
from platforms.facade import (
    PLATFORM_BY_KEY,
    get_platform,
    platform_cookie_config_names,
    platform_cookie_domains,
    platform_labels,
)


def __getattr__(name: str):
    if name == "DownloaderFacade":
        from downloader import YoutubeDownloader

        return YoutubeDownloader
    raise AttributeError(name)

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
