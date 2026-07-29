"""Bilibili platform plugin."""

from __future__ import annotations

import re
from urllib.parse import ParseResult

from platforms.base import BaseMediaPlatform, MediaInput, Platform


def extract_bilibili_id(value: str) -> str:
    bv_match = re.search(r"\b(BV[0-9A-Za-z]{10})\b", value, flags=re.I)
    if bv_match:
        raw = bv_match.group(1)
        return "BV" + raw[2:]
    av_match = re.search(r"\bav(\d+)\b", value, flags=re.I)
    if av_match:
        return f"av{av_match.group(1)}"
    return ""


class BilibiliPlatform(BaseMediaPlatform):
    key = Platform.BILIBILI
    cookie_domains = ("bilibili.com", "b23.tv")
    cookie_config_name = "bilibili_cookiefile.txt"
    probe_source = "BV1GJ411x7h7"

    def try_parse(
        self,
        original: str,
        normalized: str,
        parsed: ParseResult,
    ) -> MediaInput | None:
        host = parsed.netloc.lower()
        if "bilibili.com" in host or host.endswith("b23.tv"):
            media_id = extract_bilibili_id(normalized) or normalized
            return MediaInput(Platform.BILIBILI.value, original, media_id, normalized)

        bili_id = extract_bilibili_id(original)
        if bili_id:
            return MediaInput(
                Platform.BILIBILI.value,
                original,
                bili_id,
                f"https://www.bilibili.com/video/{bili_id}",
            )
        return None

    def http_headers(self) -> dict[str, str] | None:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.bilibili.com/",
        }
