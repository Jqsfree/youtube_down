#!/usr/bin/env python3
"""独立批量下载：默认带 Cookie、自适应节流、跳过已完成文件。

不依赖 GUI。输入可为表格文件、文件夹（递归）或直接的链接/ID；
输出目录镜像输入层级，文件名为 ``<原表行号>_<媒体ID>``，结果写入输出根的 ``下载总览.csv``。
Cookie 由 ``--cookies`` 或浏览器导出准备，下载时始终启用。

示例::

    youtube-down-cli -o ~/Downloads/out list.csv
    youtube-down-cli -o ~/Downloads/out ~/lists/        # 整个文件夹
    youtube-down-cli -o ~/Downloads/out --cookies ~/cookies.txt list.csv
"""

from __future__ import annotations

import argparse
import http.client
import json
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yt_dlp

from batch_io import (
    INPUT_EXTS,
    PASTE_SOURCE,
    InputFile,
    OverviewStore,
    discover_inputs,
    filename_stem,
    media_id_from_stem,
    media_stem,
    output_dir_for,
    parse_pasted_text,
)
from downloader import (
    BOT_STREAK_LIMIT,
    BRUTAL_YDL_OPTS,
    NORMAL_YDL_OPTS,
    YoutubeDownloader,
    classify_error,
    clean_error,
    dump_browser_cookies,
)
from pacing import AdaptivePacer

# 兼容旧测试名：正常模式与 NORMAL_YDL_OPTS 对齐（视频间间隔交给 AdaptivePacer）
CLI_YDL_OPTS: dict[str, Any] = dict(NORMAL_YDL_OPTS)

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
def _probe_short_side(path: Path) -> int | None:
    """ffprobe 视频短边；无法解析视频轨时返回 None。"""
    try:
        from downloader import _find_tool
        import subprocess
        result = subprocess.run(
            [_find_tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        parts = [p.strip() for p in result.stdout.strip().split(",") if p.strip()]
        dims = [int(p) for p in parts[:2]]
        if len(dims) >= 2:
            return min(dims)
        return dims[0] if dims else None
    except Exception:
        return None


def collect_completed_ids(*dirs: Path, min_height: int = 0) -> set[str]:
    """已有有效媒体文件的 video_id（需能解析视频轨，且短边 >= min_height）。

    同时识别旧命名 ``{id}.ext`` 与行号命名 ``NNNN_{id}.ext``。
    """
    return set(collect_completed_files(*dirs, min_height=min_height))


def collect_completed_files(*dirs: Path, min_height: int = 0) -> dict[str, Path]:
    """同 collect_completed_ids，返回 {video_id: 文件路径}。"""
    done: dict[str, Path] = {}
    for folder in dirs:
        if folder is None or not folder.exists() or folder.is_file():
            continue
        for path in folder.iterdir():
            if path.suffix.lower() not in _VIDEO_EXTS or path.stat().st_size <= 1024:
                continue
            short = _probe_short_side(path)
            if short is None:
                continue
            if min_height > 0 and short < min_height:
                continue
            done.setdefault(path.stem, path)
            done.setdefault(media_id_from_stem(path.stem), path)
    return done


def _remove_media_files(output_dir: Path, *stems: str) -> None:
    for stem in stems:
        for ext in _VIDEO_EXTS:
            (output_dir / f"{stem}{ext}").unlink(missing_ok=True)


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
    fragment_workers: int = 8,
    brutal: bool = False,
) -> YoutubeDownloader:
    overrides = dict(BRUTAL_YDL_OPTS if brutal else NORMAL_YDL_OPTS)
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
    fragment_workers: int = 8,
    brutal: bool = False,
    stem: str | None = None,
) -> dict[str, str]:
    # 续传判定未通过的同名旧文件（损坏/低清）须先删，否则 yt-dlp 会直接复用它
    _vid = YoutubeDownloader._normalize_video_id(video_id) or video_id
    stale_stems = (_vid, stem) if stem else (_vid,)
    _remove_media_files(output_dir, *stale_stems)
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
                filename_stem=stem,
                use_cookies=True,
                needs_audio_merge=True,
                min_height=min_height,
                expected_height=min_height if min_height > 0 else None,
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
            if any(
                k in msg
                for k in (
                    "低于最低要求", "与目标档位", "缺少音轨", "缺少视频轨",
                    "时长异常", "无法解析为有效媒体",
                )
            ):
                _remove_media_files(output_dir, *stale_stems)
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
    video_id: str,
    output_dir: Path,
    status: str,
    category: str,
    msg: str,
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


@dataclass
class Job:
    """一条待下载记录：source 为总览里的来源文件（相对路径），row_no 为原表行号。"""

    src: str
    source: str
    row_no: int
    out_dir: Path

    @property
    def media_id(self) -> str:
        try:
            return media_stem(YoutubeDownloader.parse_input(self.src).media_id)
        except Exception:
            return media_stem(self.src)

    @property
    def stem(self) -> str:
        return filename_stem(self.row_no, self.media_id)


def _rel_for(inp: InputFile, input_root: Path | None) -> str:
    if input_root is None:
        return inp.rel
    try:
        return inp.path.resolve().relative_to(input_root).with_suffix("").as_posix()
    except ValueError:
        return inp.rel


def _iter_sources(
    args: argparse.Namespace, output_dir: Path,
) -> tuple[list[Job], list[Job]]:
    input_root = args.input_root.expanduser().resolve() if args.input_root else None
    jobs: list[Job] = []
    pasted: list[str] = []
    for item in args.sources:
        path = Path(item).expanduser()
        if not (path.is_dir() or (path.is_file() and path.suffix.lower() in INPUT_EXTS)):
            pasted.append(item)
            continue
        inputs, errors = discover_inputs(path)
        for err in errors:
            print(f"跳过输入: {err}", file=sys.stderr, flush=True)
        for inp in inputs:
            rel = _rel_for(inp, input_root)
            out_dir = output_dir_for(output_dir, rel)
            for index, row in enumerate(inp.rows, start=1):
                row_no = int(row.get("_row") or index)
                jobs.append(Job(row["video_id"], rel, row_no, out_dir))

    valid, invalid = parse_pasted_text("\n".join(pasted))
    for token in invalid:
        print(f"跳过无效来源: {token}", file=sys.stderr, flush=True)
    paste_dir = output_dir_for(output_dir, PASTE_SOURCE)
    jobs.extend(
        Job(src, PASTE_SOURCE, index, paste_dir)
        for index, src in enumerate(valid, start=1)
    )

    seen: set[tuple[str, str]] = set()
    unique: list[Job] = []
    duplicates: list[Job] = []
    for job in jobs:
        key = (job.source, job.media_id)
        if key in seen:
            duplicates.append(job)
            continue
        seen.add(key)
        unique.append(job)
    return unique, duplicates


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量下载（默认 Cookie、自适应节流、可续传）")
    parser.add_argument("sources", nargs="+", help="CSV/TSV/TXT、包含它们的文件夹，或视频 URL/ID")
    parser.add_argument(
        "-o", "--output-dir", type=Path, required=True,
        help="输出根目录（镜像输入层级，并写入 下载总览.csv）",
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        default=None,
        help="计算输出镜像路径的输入根；默认传文件夹时以该文件夹为根，传文件时用文件名",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help="浏览器 Cookie 缓存目录，默认与输出目录相同",
    )
    parser.add_argument(
        "--workers", type=int, default=None,
        help="并行视频数（默认 1；brutal 未指定时为 8；>1 易触发 YouTube bot）",
    )
    parser.add_argument(
        "--sleep-min", type=float, default=2.0,
        help="两条视频之间最小随机等待秒数（默认 2）",
    )
    parser.add_argument(
        "--sleep-max", type=float, default=4.0,
        help="两条视频之间最大随机等待秒数（默认 4）",
    )
    parser.add_argument(
        "--fragment-workers", type=int, default=None,
        help="单条视频分片并发数（默认与正常模式一致，当前 12；brutal 未指定时为 8）",
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
        help="强制 youtube player_client=mweb（可再装 bgutil PO Token 插件；可能减少可用格式）",
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="不跳过已存在 mp4",
    )
    parser.add_argument(
        "--brutal",
        action="store_true",
        help="暴力模式：未指定时 workers/fragment-workers=8、更高重试与更短 sleep",
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
    if args.workers is None:
        args.workers = 8 if args.brutal else 1
    if args.fragment_workers is None:
        args.fragment_workers = 8 if args.brutal else int(
            NORMAL_YDL_OPTS["concurrent_fragment_downloads"]
        )
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
        print(
            "已强制 player_client=mweb（若需 PO Token 请另装 bgutil-ytdlp-pot-provider；"
            "注意 mweb 可能减少可用清晰度）",
            flush=True,
        )

    sources, duplicates = _iter_sources(args, output_dir)
    if not sources and not duplicates:
        print("没有可下载的视频", file=sys.stderr)
        return 2

    overview = OverviewStore(output_dir)
    skip_n = 0
    for dup in duplicates:
        # 总览键为 (来源, video_id)，重复行无法单独成行；只计数并提示
        print(
            f"跳过重复行: {dup.source}#{dup.row_no} {dup.media_id}",
            file=sys.stderr,
            flush=True,
        )
        skip_n += 1
    if not sources:
        print(f"没有可下载的视频（重复跳过 {skip_n}）", file=sys.stderr)
        return 0 if skip_n else 2

    def _record(job: Job, result: dict[str, str]) -> None:
        try:
            media = YoutubeDownloader.parse_input(job.src)
            platform, url = media.platform, media.url
        except Exception:
            platform, url = "", job.src
        overview.upsert(
            source=job.source,
            row_no=job.row_no,
            media_id=job.media_id,
            platform=platform,
            url=url,
            title=result.get("title") or "",
            status=result["status"],
            category=result.get("error_category", ""),
            message=result.get("error_message", ""),
            output_file=result.get("path") or "",
        )

    pending: list[Job] = []
    existing_cache: dict[Path, dict[str, Path]] = {}
    legacy_existing: dict[str, Path] = {}
    if not args.no_skip_existing:
        # 旧版平铺布局：视频直接在输出根目录
        legacy_existing = collect_completed_files(output_dir, min_height=args.min_height)
    for job in sources:
        if not args.no_skip_existing:
            if job.out_dir not in existing_cache:
                existing_cache[job.out_dir] = collect_completed_files(
                    job.out_dir, min_height=args.min_height,
                )
            found = existing_cache[job.out_dir].get(job.media_id) or legacy_existing.get(job.media_id)
            if found:
                if overview.status_of(job.source, job.media_id) != "success":
                    _record(job, {"status": "success", "path": str(found)})
                continue
        pending.append(job)

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

    print(f"下载总览: {overview.path}", flush=True)

    pacer = AdaptivePacer(args.sleep_min, args.sleep_max)
    ok = fail = 0
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

    def _download_job(job: Job) -> dict[str, str]:
        with cookie_lock:
            cf = cookiefile
        job.out_dir.mkdir(parents=True, exist_ok=True)
        return _download_one(
            job.src, job.out_dir, cf, args.min_height,
            use_pot=args.pot, fragment_workers=args.fragment_workers,
            brutal=args.brutal, stem=job.stem,
        )

    def _submit(pool: ThreadPoolExecutor, inflight: dict) -> None:
        if stop_bot:
            return
        try:
            job = next(pending_iter)
        except StopIteration:
            return
        fut = pool.submit(_download_job, job)
        inflight[fut] = job

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            inflight: dict = {}
            for _ in range(workers):
                _submit(pool, inflight)
            while inflight:
                fut = next(as_completed(inflight))
                job = inflight.pop(fut)
                src = job.src
                done_n += 1
                downloads_since_cookie_refresh += 1
                try:
                    result = fut.result()
                except Exception as exc:
                    result = _result(
                        YoutubeDownloader._normalize_video_id(src) or src,
                        job.out_dir, "failed", "UNKNOWN", clean_error(exc),
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
                            if recovered:
                                bot_streak = 0
                        if not recovered:
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

                extra = result.get("error_message") or result.get("path") or ""
                print(f"[{done_n}/{total}] {mark} {job.source}#{job.row_no} {job.media_id} {extra[:120]}", flush=True)
                _record(job, result)

                if bot_streak >= BOT_STREAK_LIMIT:
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
    except KeyboardInterrupt:
        print("已中断，重跑同一命令可续传", flush=True)

    print(f"完成 success={ok} failed={fail} skipped={skip_n} 总览={overview.path}", flush=True)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
