"""TikTok platform plugin (yt-dlp extractor)."""

from __future__ import annotations

import re
from urllib.parse import ParseResult

from platforms.base import BaseMediaPlatform, MediaInput, Platform


class TikTokPlatform(BaseMediaPlatform):
    key = Platform.TIKTOK
    cookie_domains = ("tiktok.com",)
    cookie_config_name = "tiktok_cookiefile.txt"
    # Public sample clip used for cookie probes when available.
    probe_source = "https://www.tiktok.com/@scout2015/video/6718339390858298630"

    def try_parse(
        self,
        original: str,
        normalized: str,
        parsed: ParseResult,
    ) -> MediaInput | None:
        host = parsed.netloc.lower()
        path = parsed.path or ""

        if any(
            host.endswith(h)
            for h in ("tiktok.com", "vm.tiktok.com", "vt.tiktok.com", "www.tiktok.com", "m.tiktok.com")
        ) or "tiktok.com" in host:
            video_match = re.search(r"/video/(\d+)", path)
            media_id = video_match.group(1) if video_match else (normalized or original)
            return MediaInput(Platform.TIKTOK.value, original, media_id, normalized)

        # Bare numeric aweme id — treat as TikTok only when clearly long.
        if re.fullmatch(r"\d{15,25}", original.strip()):
            media_id = original.strip()
            return MediaInput(
                Platform.TIKTOK.value,
                original,
                media_id,
                f"https://www.tiktok.com/@/video/{media_id}",
            )
        return None

    def http_headers(self) -> dict[str, str] | None:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.tiktok.com/",
        }
