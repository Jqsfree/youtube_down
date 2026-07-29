"""YouTube platform plugin."""

from __future__ import annotations

import re
from urllib.parse import ParseResult, parse_qs

from platforms.base import BaseMediaPlatform, MediaInput, Platform


class YouTubePlatform(BaseMediaPlatform):
    key = Platform.YOUTUBE
    cookie_domains = ("youtube.com", "youtu.be")
    cookie_config_name = "youtube_cookiefile.txt"
    probe_source = "dQw4w9WgXcQ"

    def try_parse(
        self,
        original: str,
        normalized: str,
        parsed: ParseResult,
    ) -> MediaInput | None:
        host = parsed.netloc.lower()
        path = parsed.path or ""

        if host.endswith("youtu.be"):
            media_id = path.strip("/").split("/")[0]
            if media_id:
                return MediaInput(
                    Platform.YOUTUBE.value,
                    original,
                    media_id,
                    f"https://www.youtube.com/watch?v={media_id}",
                )
        if "youtube.com" in host:
            query = parse_qs(parsed.query)
            media_id = (query.get("v") or [""])[0]
            if not media_id:
                match = re.search(r"/(?:shorts|embed|v)/([A-Za-z0-9_-]{11})", path)
                media_id = match.group(1) if match else ""
            if media_id:
                return MediaInput(
                    Platform.YOUTUBE.value,
                    original,
                    media_id,
                    f"https://www.youtube.com/watch?v={media_id}",
                )

        yt_match = re.search(r"^([A-Za-z0-9_-]{11})$", original.strip())
        if yt_match:
            media_id = yt_match.group(1)
            return MediaInput(
                Platform.YOUTUBE.value,
                original,
                media_id,
                f"https://www.youtube.com/watch?v={media_id}",
            )
        return None

    def requires_js_runtime_for_cookie_file(self) -> bool:
        return True
