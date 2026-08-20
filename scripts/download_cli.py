#!/usr/bin/env python3
"""独立批量下载：Cookie 直下、自适应节流、跳过已完成文件。

不依赖 GUI。续传读取输出目录里已有 mp4。

示例::

    python scripts/download_cli.py -o ~/Downloads/out list.csv
    python scripts/download_cli.py -o ~/Downloads/out --cookies ~/cookies.txt list.csv
"""

from __future__ import annotations

import argparse
import csv
import http.client
import json
import random
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import yt_dlp

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from downloader import (  # noqa: E402
    BRUTAL_YDL_OPTS,
    YoutubeDownloader,
    classify_error,
    clean_error,
)

# CLI 专用 yt-dlp 选项：禁止 sleep=0，只加速分片下载阶段
CLI_YDL_OPTS: dict[str, Any] = {
    "sleep_interval": 5,
    "max_sleep_interval": 10,
    "sleep_interval_requests": 1.0,
    "extractor_retries": 3,
    "retries": 5,
    # 只加速单条拉流，不增加 extract 频率
    "concurrent_fragment_downloads": 16,
}

# 常见机房 ASN 关键词（出口为这些时提示换住宅节点）
_DATACENTER_ORG_KEYWORDS = (
    "amazon",
    "google cloud",
    "microsoft",
    "digitalocean",
    "linode",
    "vultr",
    "oracle cloud",
    "alibaba",
    "tencent",
    "cloudflare",
    "ovh",
    "hetzner",
)

_DATACENTER_NODE_KEYWORDS = (
    "aws",
    "gcp",
    "google",
    "azure",
    "digitalocean",
    "linode",
    "vultr",
    "oracle",
    "alibaba",
    "tencent",
    "cloudflare",
    "ovh",
    "hetzner",
)
_SKIP_NODE_NAMES = {
    "DIRECT",
    "REJECT",
    "PASS",
    "COMPATIBLE",
    "自动选择",
    "故障转移",
}
_SKIP_NODE_CONTAINS = (
    "剩余流量",
    "套餐到期",
    "官网",
    "订阅",
    "电报",
    "防失联",
    "有超过",
    "不够请",
)

_VIDEO_EXTS = (".mp4", ".webm", ".mkv", ".mov", ".flv")
_RESULTS_FIELDS = [
    "video_id", "platform", "source_url", "title", "status",
    "error_category", "error_message", "cookie_used", "output_dir",
]
_DEBUG_LOG_PATH = Path("/home/jqs/projects/vid_download/.cursor/debug-136086.log")
_DEBUG_SESSION_ID = "136086"


def _debug_log(
    run_id: str,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any],
) -> None:
    payload = {
        "sessionId": _DEBUG_SESSION_ID,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    try:
        _DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def collect_completed_ids(*dirs: Path) -> set[str]:
    """已有有效媒体文件的 video_id。"""
    done: set[str] = set()
    for folder in dirs:
        if folder is None or not folder.exists() or folder.is_file():
            continue
        for path in folder.iterdir():
            if path.suffix.lower() in _VIDEO_EXTS and path.stat().st_size > 1024:
                done.add(path.stem)
    return done


def check_exit_ip(proxy: str = "http://127.0.0.1:7897") -> tuple[str, str]:
    """返回 (ip, org)；失败时 org 为空。"""
    try:
        req = urllib.request.Request("https://ipinfo.io/json")
        if proxy:
            req.set_proxy(proxy.replace("http://", "").replace("https://", ""), "http")
        with urllib.request.urlopen(req, timeout=8) as resp:
            import json
            data = json.loads(resp.read().decode())
            return str(data.get("ip", "")), str(data.get("org", ""))
    except Exception:
        return "", ""


def warn_datacenter_ip(org: str) -> None:
    lower = org.lower()
    if any(kw in lower for kw in _DATACENTER_ORG_KEYWORDS):
        print(
            f"警告: 当前代理出口疑似机房 IP ({org})。"
            "YouTube 批量下载建议切换 Clash 到住宅/家庭宽带节点后再跑。",
            file=sys.stderr,
            flush=True,
        )


def is_rotatable_clash_node(name: str) -> bool:
    """是否适合作为 YouTube 出口节点（排除机房/占位项）。"""
    text = (name or "").strip()
    if not text or text in _SKIP_NODE_NAMES:
        return False
    if any(token in text for token in _SKIP_NODE_CONTAINS):
        return False
    lower = text.lower()
    if any(kw in lower for kw in _DATACENTER_NODE_KEYWORDS):
        return False
    if lower.endswith("do"):  # DigitalOcean 简称，如 IPv6美国07do
        return False
    return True


def pick_next_clash_node(current: str, candidates: list[str], tried: set[str]) -> str | None:
    """从候选里挑下一个未试过的非机房节点。"""
    usable = [name for name in candidates if is_rotatable_clash_node(name)]
    unused = [name for name in usable if name not in tried and name != current]
    if unused:
        return unused[0]
    leftover = [name for name in usable if name != current]
    return leftover[0] if leftover else None


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 8.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self._unix_path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._unix_path)
        self.sock = sock


class ClashClient:
    """Clash/Mihomo 外部控制器（优先 Unix socket）。"""

    def __init__(
        self,
        *,
        unix_socket: str = "/tmp/verge/verge-mihomo.sock",
        secret: str = "set-your-secret",
        group: str = "飞鸟云",
    ) -> None:
        self.unix_socket = unix_socket
        self.secret = secret
        self.group = group

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        conn = _UnixHTTPConnection(self.unix_socket)
        headers = {"Authorization": f"Bearer {self.secret}"}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            if resp.status >= 400:
                raise RuntimeError(f"Clash API {method} {path} -> {resp.status}: {raw[:200]!r}")
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
        finally:
            conn.close()

    def group_now(self) -> tuple[str, list[str]]:
        data = self._request("GET", "/proxies") or {}
        proxies = data.get("proxies") or {}
        info = proxies.get(self.group)
        if not info:
            raise RuntimeError(f"Clash 找不到策略组: {self.group}")
        return str(info.get("now") or ""), list(info.get("all") or [])

    def switch(self, node: str) -> None:
        encoded = urllib.parse.quote(self.group, safe="")
        self._request("PUT", f"/proxies/{encoded}", {"name": node})
        try:
            self._request("DELETE", "/connections")
        except Exception:
            pass

    def rotate(self, tried: set[str]) -> str | None:
        current, candidates = self.group_now()
        nxt = pick_next_clash_node(current, candidates, tried)
        if not nxt:
            return None
        self.switch(nxt)
        tried.add(nxt)
        return nxt


def dump_browser_cookies(browser_spec: str, dest: Path) -> Path:
    """从浏览器导出 Netscape cookies。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    parts = tuple(p for p in browser_spec.split(":") if p)
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "skip_download": True,
        "cookiesfrombrowser": parts,
        "cookiefile": str(dest),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            ydl.cookiejar  # 触发从浏览器加载
        except Exception:
            pass
        cookiejar = getattr(ydl, "cookiejar", None)
        if cookiejar is not None and hasattr(cookiejar, "save"):
            cookiejar.save(filename=str(dest), ignore_discard=True, ignore_expires=True)
    if not dest.is_file() or dest.stat().st_size < 80:
        raise RuntimeError(
            f"无法从浏览器导出 Cookie（{browser_spec}）。"
            "请关闭 Chrome 后重试，或改用 --cookies cookies.txt"
        )
    return dest


def _pot_extractor_args(enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {}
    # bgutil 等 PO Token 插件安装后会自动 hook；显式指定 mweb 客户端
    return {
        "extractor_args": {
            "youtube": {"player_client": ["mweb"]},
        },
    }


def _make_downloader(
    cookiefile: Path,
    *,
    use_pot: bool = False,
    fragment_workers: int = 16,
    brutal: bool = False,
) -> YoutubeDownloader:
    overrides = dict(BRUTAL_YDL_OPTS if brutal else CLI_YDL_OPTS)
    overrides["concurrent_fragment_downloads"] = max(1, fragment_workers)
    overrides.update(_pot_extractor_args(use_pot))
    return YoutubeDownloader(
        cookies_from_browser="",
        cookiefile=cookiefile,
        ydl_opt_overrides=overrides,
    )


def _download_one(
    video_id: str,
    output_dir: Path,
    cookiefile: Path,
    min_height: int,
    *,
    use_pot: bool = False,
    fragment_workers: int = 16,
    run_id: str = "run-unknown",
    brutal: bool = False,
) -> dict[str, str]:
    # #region agent log
    _debug_log(
        run_id,
        "H3",
        "scripts/download_cli.py:_download_one:entry",
        "download-start",
        {
            "video_id": video_id,
            "fragment_workers": fragment_workers,
            "cookiefile": str(cookiefile),
            "cookie_exists": cookiefile.exists(),
            "cookie_size": cookiefile.stat().st_size if cookiefile.exists() else -1,
        },
    )
    # #endregion
    dl = _make_downloader(
        cookiefile, use_pot=use_pot, fragment_workers=fragment_workers, brutal=brutal,
    )
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            path = dl.download(
                video_id=video_id,
                format_id="best",
                output_dir=output_dir,
                use_cookies=True,
                needs_audio_merge=True,
                min_height=min_height,
                expected_height=min_height,
                strict_quality=True,
                brutal=brutal,
            )
            return {
                "video_id": video_id,
                "status": "success",
                "error_category": "SUCCESS",
                "error_message": "",
                "cookie_used": "true",
                "output_dir": str(output_dir),
                "title": "",
                "path": str(path),
            }
        except Exception as exc:
            last_exc = exc
            cat = classify_error(exc)
            msg = clean_error(exc)
            # #region agent log
            _debug_log(
                run_id,
                "H1",
                "scripts/download_cli.py:_download_one:except",
                "download-error",
                {
                    "video_id": video_id,
                    "attempt": attempt,
                    "category": cat.code,
                    "message_head": msg[:200],
                },
            )
            # #endregion
            if "低于最低要求" in msg or ("低于" in msg and str(min_height) in msg):
                return _result(video_id, output_dir, "skipped", "SKIPPED", msg)
            if "requested format is not available" in msg.lower():
                return _result(video_id, output_dir, "skipped", "SKIPPED", msg)
            if cat.code == "RATE_LIMIT" and attempt == 0:
                time.sleep(10)
                continue
            return _result(
                video_id, output_dir,
                "failed" if cat.code != "SKIPPED" else "skipped",
                cat.code, msg,
            )
    assert last_exc is not None
    cat = classify_error(last_exc)
    return _result(video_id, output_dir, "failed", cat.code, clean_error(last_exc))


def _result(
    video_id: str, output_dir: Path, status: str, category: str, msg: str,
) -> dict[str, str]:
    return {
        "video_id": video_id,
        "status": status,
        "error_category": category,
        "error_message": msg,
        "cookie_used": "true",
        "output_dir": str(output_dir),
        "title": "",
        "path": "",
    }


def _iter_sources(args: argparse.Namespace) -> list[str]:
    sources: list[str] = []
    for item in args.sources:
        path = Path(item).expanduser()
        if path.is_file() and path.suffix.lower() in {".csv", ".tsv", ".txt"}:
            sources.extend(YoutubeDownloader.load_csv(path))
        else:
            sources.append(item)
    seen: set[str] = set()
    unique: list[str] = []
    for src in sources:
        vid = YoutubeDownloader._normalize_video_id(src) or src
        if vid in seen:
            continue
        seen.add(vid)
        unique.append(src)
    return unique


def _open_results_csv(results_dir: Path) -> tuple[Path, csv.DictWriter, Any]:
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    csv_path = results_dir / f"batch_results_{stamp}.csv"
    handle = csv_path.open("w", newline="", encoding="utf-8")
    writer = csv.DictWriter(handle, fieldnames=_RESULTS_FIELDS, extrasaction="ignore")
    writer.writeheader()
    handle.flush()
    return csv_path, writer, handle


def _write_failed_csv(results_dir: Path, rows: list[dict[str, str]]) -> str:
    if not rows:
        return ""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = results_dir / f"failed_videos_{stamp}.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_RESULTS_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return str(path)


class AdaptivePacer:
    """自适应批量间隔：连续成功略提速，bot/429 退避。"""

    def __init__(
        self,
        sleep_min: float,
        sleep_max: float,
        *,
        ok_streak_to_speedup: int = 15,
        speedup_step: float = 0.5,
        min_floor: float = 1.5,
        max_ceiling: float = 30.0,
        bot_pause_sec: float = 120.0,
    ) -> None:
        self.sleep_min = sleep_min
        self.sleep_max = sleep_max
        self.ok_streak = 0
        self.ok_streak_to_speedup = ok_streak_to_speedup
        self.speedup_step = speedup_step
        self.min_floor = min_floor
        self.max_ceiling = max_ceiling
        self.bot_pause_sec = bot_pause_sec

    def wait(self) -> None:
        if self.sleep_max <= 0:
            return
        lo = max(0.0, self.sleep_min)
        hi = max(lo, self.sleep_max)
        time.sleep(random.uniform(lo, hi))

    def on_success(self) -> None:
        self.ok_streak += 1
        if self.ok_streak >= self.ok_streak_to_speedup:
            self.sleep_min = max(self.min_floor, self.sleep_min - self.speedup_step)
            self.sleep_max = max(self.sleep_min, self.sleep_max - self.speedup_step)
            self.ok_streak = 0

    def on_skip(self) -> None:
        self.ok_streak += 1

    def on_bot_or_rate_limit(self) -> None:
        self.ok_streak = 0
        self.sleep_min = min(self.max_ceiling, max(self.min_floor, self.sleep_min * 2))
        self.sleep_max = min(self.max_ceiling, max(self.sleep_min, self.sleep_max * 2))
        print(
            f"触发限流/bot，退避 {self.bot_pause_sec:.0f}s，"
            f"间隔调整为 {self.sleep_min:.1f}-{self.sleep_max:.1f}s",
            flush=True,
        )
        time.sleep(self.bot_pause_sec)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cookie 直下批量下载（自适应节流、可续传）")
    parser.add_argument("sources", nargs="+", help="CSV/TXT 或视频 URL/ID")
    parser.add_argument("-o", "--output-dir", type=Path, required=True, help="视频输出目录")
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help="batch_results CSV 目录，默认与输出目录相同",
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="并行视频数（默认 1；>1 易触发 YouTube bot）",
    )
    parser.add_argument(
        "--sleep-min", type=float, default=5.0,
        help="两条视频之间最小随机等待秒数（官方建议 5–10s）",
    )
    parser.add_argument(
        "--sleep-max", type=float, default=10.0,
        help="两条视频之间最大随机等待秒数",
    )
    parser.add_argument(
        "--fragment-workers", type=int, default=16,
        help="单条视频分片并发数（只加速拉流，默认 16）",
    )
    parser.add_argument("--min-height", type=int, default=720)
    parser.add_argument(
        "--cookies-from-browser",
        default="chrome",
        help="从浏览器导出 Cookie（workers=1 且未指定 --cookies 时）",
    )
    parser.add_argument(
        "--cookies", type=Path, default=None,
        help="Netscape cookies.txt（推荐无痕窗口导出，见 yt-dlp Wiki）",
    )
    parser.add_argument(
        "--refresh-cookies-every",
        type=int,
        default=100,
        help="每 N 条从浏览器重新导出 Cookie；0 表示不自动刷新（默认 100）",
    )
    parser.add_argument(
        "--proxy-check-url",
        default="http://127.0.0.1:7897",
        help="启动时检查出口 IP 的代理（设为空字符串跳过检查）",
    )
    parser.add_argument(
        "--pot",
        action="store_true",
        help="启用 YouTube PO Token 模式（需安装 bgutil-ytdlp-pot-provider 插件）",
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="不跳过已存在 mp4",
    )
    parser.add_argument(
        "--brutal",
        action="store_true",
        help="暴力模式：workers=8、fragment-workers=8、更高重试与更短 sleep",
    )
    parser.add_argument(
        "--no-auto-recover",
        action="store_true",
        help="bot 后不自动换 Clash 节点、不自动重新导出 Cookie",
    )
    parser.add_argument(
        "--clash-socket",
        default="/tmp/verge/verge-mihomo.sock",
        help="Clash/Mihomo Unix socket（默认 Clash Verge）",
    )
    parser.add_argument(
        "--clash-secret",
        default="set-your-secret",
        help="Clash API secret",
    )
    parser.add_argument(
        "--clash-group",
        default="飞鸟云",
        help="要轮换的 Clash 策略组名",
    )
    parser.add_argument(
        "--max-recoveries",
        type=int,
        default=8,
        help="单次运行最多自动换节点/刷新 Cookie 次数",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def _resolve_cookiefile(
    args: argparse.Namespace,
    results_dir: Path,
    *,
    force_refresh: bool = False,
) -> Path:
    dest = (
        args.cookies.expanduser().resolve()
        if args.cookies
        else (results_dir / ".yt_cookies.txt")
    )
    if args.cookies and not force_refresh:
        if not dest.is_file():
            raise FileNotFoundError(f"Cookie 文件不存在: {dest}")
        return dest
    # When user provides --cookies explicitly, never try to re-export from browser.
    # In auto-recovery we only rotate node; user can re-export cookies manually and re-run.
    if args.cookies:
        if not dest.is_file():
            raise FileNotFoundError(f"Cookie 文件不存在: {dest}")
        return dest
    print(f"从浏览器导出 Cookie: {args.cookies_from_browser}", flush=True)
    dump_browser_cookies(args.cookies_from_browser, dest)
    print(f"Cookie 已写入 {dest} ({dest.stat().st_size} bytes)", flush=True)
    return dest


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.brutal:
        args.workers = 8
        args.fragment_workers = 8
    run_id = f"run-{int(time.time())}"
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    results_dir = (args.results_dir or output_dir).expanduser().resolve()
    results_dir.mkdir(parents=True, exist_ok=True)

    if args.proxy_check_url:
        ip, org = check_exit_ip(args.proxy_check_url)
        if ip:
            print(f"代理出口: {ip} ({org})", flush=True)
            warn_datacenter_ip(org)

    if args.pot:
        print("PO Token 模式已启用（需已安装 bgutil-ytdlp-pot-provider 等插件）", flush=True)

    sources = _iter_sources(args)
    if not sources:
        print("没有可下载的视频", file=sys.stderr)
        return 2

    skip: set[str] = set()
    if not args.no_skip_existing:
        skip = collect_completed_ids(output_dir, results_dir, output_dir.parent)
    pending = []
    for src in sources:
        vid = YoutubeDownloader._normalize_video_id(src) or src
        if vid in skip:
            continue
        pending.append(src)

    print(
        f"总计 {len(sources)}，已完成/跳过 {len(sources) - len(pending)}，"
        f"待下载 {len(pending)}，workers={args.workers}，"
        f"sleep={args.sleep_min}-{args.sleep_max}s，"
        f"fragments={args.fragment_workers}",
        flush=True,
    )
    if not pending:
        print("没有剩余视频")
        return 0

    try:
        cookiefile = _resolve_cookiefile(args, results_dir)
        # #region agent log
        _debug_log(
            run_id,
            "H2",
            "scripts/download_cli.py:main:cookie-ready",
            "cookie-ready",
            {
                "cookiefile": str(cookiefile),
                "cookie_exists": cookiefile.exists(),
                "cookie_size": cookiefile.stat().st_size if cookiefile.exists() else -1,
                "cookie_mtime": cookiefile.stat().st_mtime if cookiefile.exists() else 0,
                "workers": args.workers,
                "sleep_min": args.sleep_min,
                "sleep_max": args.sleep_max,
                "fragment_workers": args.fragment_workers,
                "pending_count": len(pending),
                "pending_first3": pending[:3],
            },
        )
        # #endregion
        if args.cookies:
            print(f"使用 Cookie 文件: {cookiefile}", flush=True)
            print(
                "提示: 稳定 Cookie 请用无痕窗口登录 YouTube 后导出 cookies.txt，"
                "详见 yt-dlp Wiki Extractors#exporting-youtube-cookies",
                flush=True,
            )
    except Exception as exc:
        print(f"Cookie 准备失败: {exc}", file=sys.stderr)
        return 4

    csv_path, writer, handle = _open_results_csv(results_dir)
    csv_lock = threading.Lock()
    print(f"结果 CSV: {csv_path}", flush=True)

    pacer = AdaptivePacer(args.sleep_min, args.sleep_max)
    ok = fail = skip_n = 0
    failed_rows: list[dict[str, str]] = []
    workers = max(1, args.workers)
    pending_iter = iter(pending)
    total = len(pending)
    done_n = 0
    bot_streak = 0
    stop_bot = False
    downloads_since_cookie_refresh = 0
    cookie_lock = threading.Lock()
    use_browser_cookies = args.cookies is None
    clash = ClashClient(
        unix_socket=args.clash_socket,
        secret=args.clash_secret,
        group=args.clash_group,
    )
    tried_nodes: set[str] = set()
    recoveries = 0
    auto_recover = not args.no_auto_recover

    def _write_row(src: str, result: dict[str, str]) -> None:
        media = YoutubeDownloader.parse_input(src)
        row = {
            "video_id": result.get("video_id") or media.media_id,
            "platform": media.platform,
            "source_url": media.url,
            "title": result.get("title") or "",
            "status": result["status"],
            "error_category": result["error_category"],
            "error_message": result["error_message"],
            "cookie_used": result["cookie_used"],
            "output_dir": result["output_dir"],
        }
        with csv_lock:
            writer.writerow(row)
            handle.flush()

    def _maybe_refresh_cookies() -> None:
        nonlocal cookiefile, downloads_since_cookie_refresh
        if args.cookies:
            return
        if args.refresh_cookies_every <= 0:
            return
        if downloads_since_cookie_refresh < args.refresh_cookies_every:
            return
        with cookie_lock:
            if downloads_since_cookie_refresh < args.refresh_cookies_every:
                return
            print(f"已处理 {downloads_since_cookie_refresh} 条，刷新浏览器 Cookie…", flush=True)
            try:
                cookiefile = _resolve_cookiefile(args, results_dir, force_refresh=True)
                downloads_since_cookie_refresh = 0
            except Exception as exc:
                print(f"Cookie 刷新失败: {exc}", flush=True)

    def _download_job(src: str) -> dict[str, str]:
        with cookie_lock:
            cf = cookiefile
        return _download_one(
            src, output_dir, cf, args.min_height,
            use_pot=args.pot, fragment_workers=args.fragment_workers, run_id=run_id,
            brutal=args.brutal,
        )

    def _submit(pool: ThreadPoolExecutor, inflight: dict) -> None:
        if stop_bot:
            return
        try:
            src = next(pending_iter)
        except StopIteration:
            return
        fut = pool.submit(_download_job, src)
        inflight[fut] = src

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            inflight: dict = {}
            for _ in range(workers):
                _submit(pool, inflight)
            while inflight:
                fut = next(as_completed(inflight))
                src = inflight.pop(fut)
                done_n += 1
                downloads_since_cookie_refresh += 1
                try:
                    result = fut.result()
                except Exception as exc:
                    result = _result(
                        YoutubeDownloader._normalize_video_id(src) or src,
                        output_dir, "failed", "UNKNOWN", clean_error(exc),
                    )
                status = result["status"]
                err = (result.get("error_message") or "").lower()
                is_bot = (
                    "not a bot" in err
                    or result.get("error_category") == "BOT_VERIFICATION"
                )
                is_rate = result.get("error_category") == "RATE_LIMIT"

                if status == "success":
                    ok += 1
                    mark = "OK"
                    bot_streak = 0
                    pacer.on_success()
                elif status == "skipped":
                    skip_n += 1
                    mark = "SKIP"
                    bot_streak = 0
                    pacer.on_skip()
                else:
                    fail += 1
                    mark = "FAIL"
                    media = YoutubeDownloader.parse_input(src)
                    failed_rows.append({
                        "video_id": result.get("video_id") or media.media_id,
                        "platform": media.platform,
                        "source_url": media.url,
                        "title": "",
                        "status": status,
                        "error_category": result["error_category"],
                        "error_message": result["error_message"],
                        "cookie_used": "true",
                        "output_dir": str(output_dir),
                    })
                    if is_bot or is_rate:
                        bot_streak += 1
                        recovered = False
                        if auto_recover and recoveries < args.max_recoveries:
                            recoveries += 1
                            new_node = ""
                            try:
                                new_node = clash.rotate(tried_nodes) or ""
                                if new_node:
                                    print(f"bot 后切换 Clash 节点: {args.clash_group} -> {new_node}", flush=True)
                            except Exception as exc:
                                print(f"Clash 换节点失败: {exc}", flush=True)
                            try:
                                with cookie_lock:
                                    cookiefile = _resolve_cookiefile(
                                        args, results_dir, force_refresh=True,
                                    )
                                    downloads_since_cookie_refresh = 0
                                recovered = True
                            except Exception as exc:
                                print(f"bot 后 Cookie 刷新失败: {exc}", flush=True)
                            ip, org = ("", "")
                            if args.proxy_check_url:
                                ip, org = check_exit_ip(args.proxy_check_url)
                                if ip:
                                    print(f"换节点后出口: {ip} ({org})", flush=True)
                                    warn_datacenter_ip(org)
                            # #region agent log
                            _debug_log(
                                run_id,
                                "H7",
                                "scripts/download_cli.py:main:auto-recover",
                                "bot-auto-recover",
                                {
                                    "video_id": result.get("video_id") or src,
                                    "new_node": new_node,
                                    "cookie_size": cookiefile.stat().st_size if cookiefile.exists() else -1,
                                    "exit_ip": ip,
                                    "exit_org": org,
                                    "recoveries": recoveries,
                                    "recovered": recovered,
                                },
                            )
                            # #endregion
                            if recovered:
                                bot_streak = 0
                        if not recovered:
                            # #region agent log
                            _debug_log(
                                run_id,
                                "H4",
                                "scripts/download_cli.py:main:bot-backoff",
                                "bot-or-rate-limit",
                                {
                                    "video_id": result.get("video_id") or src,
                                    "error_category": result.get("error_category"),
                                    "bot_streak": bot_streak,
                                    "use_browser_cookies": use_browser_cookies,
                                    "sleep_min": pacer.sleep_min,
                                    "sleep_max": pacer.sleep_max,
                                    "recoveries": recoveries,
                                },
                            )
                            # #endregion
                            pacer.on_bot_or_rate_limit()
                            if use_browser_cookies and is_bot and not auto_recover:
                                try:
                                    with cookie_lock:
                                        cookiefile = _resolve_cookiefile(
                                            args, results_dir, force_refresh=True,
                                        )
                                        downloads_since_cookie_refresh = 0
                                except Exception as exc:
                                    print(f"bot 后 Cookie 刷新失败: {exc}", flush=True)
                    else:
                        bot_streak = 0

                vid = result.get("video_id") or src
                extra = result.get("error_message") or result.get("path") or ""
                print(f"[{done_n}/{total}] {mark} {vid} {extra[:120]}", flush=True)
                _write_row(src, result)

                if bot_streak >= 2:
                    stop_bot = True
                    print(
                        "连续触发 YouTube 机器人验证/限流，已停止以免刷爆。"
                        "请换住宅节点、更新 cookies.txt，过几分钟后重跑同一命令续传。",
                        flush=True,
                    )
                    for leftover in inflight:
                        leftover.cancel()
                    inflight.clear()
                    break

                _maybe_refresh_cookies()
                pacer.wait()
                _submit(pool, inflight)
    finally:
        handle.close()

    failed_path = _write_failed_csv(results_dir, failed_rows)
    if failed_path:
        print(f"失败列表: {failed_path}", flush=True)
    print(f"完成 success={ok} failed={fail} skipped={skip_n} csv={csv_path}", flush=True)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
