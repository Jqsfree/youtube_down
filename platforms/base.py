"""Platform plugin base types for multi-site downloads."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import ParseResult


class Platform(str, Enum):
    YOUTUBE = "youtube"
    BILIBILI = "bilibili"
    TIKTOK = "tiktok"
    GENERIC = "generic"

    @property
    def label(self) -> str:
        return {
            Platform.YOUTUBE: "YouTube",
            Platform.BILIBILI: "Bilibili",
            Platform.TIKTOK: "TikTok",
            Platform.GENERIC: "Generic",
        }[self]


@dataclass(frozen=True)
class MediaInput:
    """Normalized media source resolved from a user-provided ID or URL."""

    platform: str
    original: str
    media_id: str
    url: str

    @property
    def label(self) -> str:
        try:
            return Platform(self.platform).label
        except ValueError:
            return self.platform


class BaseMediaPlatform(ABC):
    """Platform-specific parse / cookie / header hooks used by the shared yt-dlp core."""

    key: Platform
    cookie_domains: tuple[str, ...]
    cookie_config_name: str
    probe_source: str

    @abstractmethod
    def try_parse(
        self,
        original: str,
        normalized: str,
        parsed: ParseResult,
    ) -> MediaInput | None:
        """Return MediaInput if this platform owns the input; else None."""

    def http_headers(self) -> dict[str, str] | None:
        return None

    def requires_js_runtime_for_cookie_file(self) -> bool:
        return False
