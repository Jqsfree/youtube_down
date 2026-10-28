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


# 分P 内部 ID：BV1xx_p2（保留页码，输出文件名才不会丢集）
_PART_RE = re.compile(r"\b(BV[0-9A-Za-z]{10}|av\d+)_p(\d+)\b", flags=re.I)


def _parse_part(value: str) -> MediaInput | None:
    match = _PART_RE.search(value or "")
    if not match:
        return None
    vid, page = match.group(1), match.group(2)
    return MediaInput(
        Platform.BILIBILI.value,
        value,
        f"{vid}_p{page}",
        f"https://www.bilibili.com/video/{vid}?p={page}",
    )


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
            part = _parse_part(normalized)
            if part is not None:
                return part
            media_id = extract_bilibili_id(normalized) or normalized
            return MediaInput(Platform.BILIBILI.value, original, media_id, normalized)

        part = _parse_part(original)
        if part is not None:
            return part
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
