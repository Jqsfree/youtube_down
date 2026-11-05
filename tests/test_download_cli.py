import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from download_cli import (  # noqa: E402
    AdaptivePacer,
    CLI_YDL_OPTS,
    collect_completed_ids,
    is_rotatable_clash_node,
    pick_next_clash_node,
    warn_datacenter_ip,
)
from downloader import NORMAL_YDL_OPTS  # noqa: E402
from pacing import AdaptivePacer as SharedAdaptivePacer  # noqa: E402


def test_collect_completed_ids_requires_valid_media(tmp_path: Path, monkeypatch) -> None:
    import download_cli

    out = tmp_path / "videos"
    out.mkdir()
    (out / "AAA111aaaaa.mp4").write_bytes(b"x" * 2048)
    (out / "garbage0000.mp4").write_bytes(b"x" * 2048)
    (out / "lowres00000.mp4").write_bytes(b"x" * 2048)
    (out / "tiny.mp4").write_bytes(b"no")
    sides = {"AAA111aaaaa.mp4": 720, "garbage0000.mp4": None, "lowres00000.mp4": 576}
    monkeypatch.setattr(
        download_cli, "valid_resume_media",
        lambda p, **kw: sides.get(p.name) is not None
        and sides[p.name] >= kw["min_height"],
    )

    done = collect_completed_ids(out, min_height=720)

    assert done == {"AAA111aaaaa"}


def test_collect_completed_ids_recognizes_row_prefixed_names(tmp_path: Path, monkeypatch) -> None:
    import download_cli

    (tmp_path / "0003_AbZ9o3oWYjo.mp4").write_bytes(b"x" * 2048)
    monkeypatch.setattr(download_cli, "valid_resume_media", lambda p, **kw: True)

    done = collect_completed_ids(tmp_path, min_height=720)

    assert "AbZ9o3oWYjo" in done


def test_iter_sources_mirrors_folder_and_pastes(tmp_path: Path) -> None:
    import argparse

    import download_cli

    src = tmp_path / "in"
    (src / "舞蹈").mkdir(parents=True)
    (src / "舞蹈" / "擦边.csv").write_text(
        "url\nhttps://www.youtube.com/watch?v=dQw4w9WgXcQ\n\nhttps://youtu.be/RajrwxvF4Yw\n",
        encoding="utf-8",
    )
    (src / "top.txt").write_text("9xK8sxOIpOQ\n", encoding="utf-8")
    out = tmp_path / "out"
    args = argparse.Namespace(
        sources=[str(src), "https://www.youtube.com/shorts/S4d2AB_unnw"], input_root=None,
    )

    jobs, dups = download_cli._iter_sources(args, out)
    assert dups == []

    by_id = {job.media_id: job for job in jobs}
    assert by_id["dQw4w9WgXcQ"].out_dir == out / "舞蹈" / "擦边"
    assert by_id["dQw4w9WgXcQ"].source == "舞蹈/擦边"
    assert by_id["RajrwxvF4Yw"].stem == "RajrwxvF4Yw"
    assert by_id["9xK8sxOIpOQ"].out_dir == out / "top"
    assert by_id["S4d2AB_unnw"].out_dir == out / "paste"
    assert by_id["S4d2AB_unnw"].stem == "S4d2AB_unnw"


def test_iter_sources_reports_duplicate_rows(tmp_path: Path) -> None:
    """表内去重后，同一输入路径重复出现仍应被 CLI 第二层去重并汇报。"""
    import argparse

    import download_cli

    csv_path = tmp_path / "dup.csv"
    csv_path.write_text(
        "url\nhttps://www.youtube.com/watch?v=dQw4w9WgXcQ\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(sources=[str(csv_path), str(csv_path)], input_root=None)
    jobs, dups = download_cli._iter_sources(args, tmp_path / "out")
    assert len(jobs) == 1
    assert len(dups) == 1
    assert jobs[0].media_id == dups[0].media_id == "dQw4w9WgXcQ"


def test_cli_ydl_opts_aligns_with_normal() -> None:
    assert CLI_YDL_OPTS == NORMAL_YDL_OPTS
    assert CLI_YDL_OPTS["sleep_interval"] >= 1
    assert CLI_YDL_OPTS["max_sleep_interval"] >= CLI_YDL_OPTS["sleep_interval"]
    assert CLI_YDL_OPTS["sleep_interval_requests"] >= 0.5
    assert CLI_YDL_OPTS["concurrent_fragment_downloads"] == 12


def test_adaptive_pacer_reexported_from_pacing() -> None:
    assert AdaptivePacer is SharedAdaptivePacer


def test_adaptive_pacer_speeds_up_after_ok_streak() -> None:
    pacer = AdaptivePacer(3.0, 6.0, ok_streak_to_speedup=3, speedup_step=0.5)
    for _ in range(3):
        pacer.on_success()
    assert pacer.sleep_min == 2.5
    assert pacer.sleep_max == 5.5


def test_adaptive_pacer_backoff_on_bot() -> None:
    pacer = AdaptivePacer(2.0, 4.0, bot_pause_sec=0.01)
    pacer.on_bot_or_rate_limit()
    assert pacer.sleep_min == 4.0
    assert pacer.sleep_max == 8.0


def test_brutal_does_not_override_explicit_workers(monkeypatch) -> None:
    import download_cli

    args = download_cli.parse_args(
        ["--brutal", "--workers", "3", "--fragment-workers", "4", "-o", "/tmp/out", "dQw4w9WgXcQ"]
    )
    assert args.brutal is True
    assert args.workers == 3
    assert args.fragment_workers == 4


def test_brutal_defaults_workers_when_omitted() -> None:
    import download_cli

    args = download_cli.parse_args(["--brutal", "-o", "/tmp/out", "dQw4w9WgXcQ"])
    # parse_args 本身不填默认；main 里填。这里验证 None 语义。
    assert args.workers is None
    assert args.fragment_workers is None


def test_download_one_always_uses_cookies(tmp_path, monkeypatch) -> None:
    import download_cli

    cookie = tmp_path / "cookies.txt"
    cookie.write_text("# Netscape\n.youtube.com\tTRUE\t/\tFALSE\t0\tSID\tx\n" * 5)
    calls: list[bool] = []

    def fake_download(*args, use_cookies=True, **kwargs):
        calls.append(use_cookies)
        out = tmp_path / "ok.mp4"
        out.write_bytes(b"0" * 2048)
        return out

    class FakeDL:
        def download(self, *a, **k):
            return fake_download(*a, **k)

    monkeypatch.setattr(download_cli, "_make_downloader", lambda *a, **k: FakeDL())

    result = download_cli._download_one("dQw4w9WgXcQ", tmp_path, cookie, 0)
    assert calls == [True]
    assert result["status"] == "success"
    assert result["cookie_used"] == "true"


def test_main_rejects_output_path_that_is_a_file(tmp_path, capsys) -> None:
    import download_cli

    not_a_dir = tmp_path / "out"
    not_a_dir.write_text("x")
    rc = download_cli.main(["-o", str(not_a_dir), "dQw4w9WgXcQ"])

    assert rc == 2
    assert "目录不可用" in capsys.readouterr().err


def test_warn_datacenter_ip(capsys) -> None:
    warn_datacenter_ip("AS16509 Amazon.com, Inc.")
    err = capsys.readouterr().err
    assert "机房" in err or "Amazon" in err


def test_rotatable_clash_node_skips_aws_and_placeholders() -> None:
    assert is_rotatable_clash_node("hy2台湾01")
    assert not is_rotatable_clash_node("日本03aws")
    assert not is_rotatable_clash_node("IPv6美国07do")
    assert not is_rotatable_clash_node("DIRECT")
    assert not is_rotatable_clash_node("剩余流量：260.45 GB")


def test_pick_next_clash_node_prefers_unused_residential() -> None:
    candidates = ["日本03aws", "自动选择", "hy2台湾01", "hy2台湾02"]
    nxt = pick_next_clash_node("日本03aws", candidates, set())
    assert nxt == "hy2台湾01"
    nxt2 = pick_next_clash_node("hy2台湾01", candidates, {"hy2台湾01"})
    assert nxt2 == "hy2台湾02"


def test_default_cli_does_not_export_browser_cookies(tmp_path, monkeypatch):
    import download_cli
    args = download_cli.parse_args(["dQw4w9WgXcQ", "-o", str(tmp_path)])
    monkeypatch.setattr(download_cli, "dump_browser_cookies", lambda *a: (_ for _ in ()).throw(AssertionError("unexpected export")))
    assert args.cookies_from_browser == ""
    assert download_cli._resolve_cookiefile(args, tmp_path) is None


def test_browser_refresh_keeps_old_snapshot_for_inflight_jobs(tmp_path, monkeypatch):
    import argparse
    import download_cli
    args = argparse.Namespace(cookies=None, cookies_from_browser="chrome", _cookie_temp_dir=tmp_path)
    monkeypatch.setattr(download_cli, "dump_browser_cookies", lambda browser, path: path.write_text("cookie snapshot"))
    first = download_cli._resolve_cookiefile(args, tmp_path)
    second = download_cli._resolve_cookiefile(args, tmp_path, force_refresh=True)
    assert first != second
    assert first.read_text() == "cookie snapshot"
    assert second.read_text() == "cookie snapshot"


def test_cli_cookie_session_cleans_up_on_exception(tmp_path, monkeypatch):
    import download_cli
    import pytest
    seen = []
    def fail(args):
        seen.append(args._cookie_temp_dir)
        (args._cookie_temp_dir / "cookies.txt").write_text("fake cookie")
        raise RuntimeError("cancelled")
    monkeypatch.setattr(download_cli, "_run", fail)
    with pytest.raises(RuntimeError, match="cancelled"):
        download_cli.main(["dQw4w9WgXcQ", "-o", str(tmp_path)])
    assert not seen[0].exists()


def test_no_cookie_failed_rotation_still_backs_off_and_stops(tmp_path, monkeypatch):
    import download_cli
    args = download_cli.parse_args(["unused", "-o", str(tmp_path), "--workers", "1"])
    args.proxy_check_url = ""
    monkeypatch.setattr(download_cli, "parse_args", lambda argv: args)
    jobs = [download_cli.Job(f"https://example.com/{n}.mp4", "paste", n, tmp_path)
            for n in range(download_cli.BOT_STREAK_LIMIT + 2)]
    monkeypatch.setattr(download_cli, "_iter_sources", lambda *a: (jobs, []))
    monkeypatch.setattr(download_cli, "collect_completed_files", lambda *a, **k: {})
    monkeypatch.setattr(download_cli.ClashClient, "rotate", lambda *a: None)
    backoffs = []
    class Pacer:
        def __init__(self, *args): pass
        def on_bot_or_rate_limit(self): backoffs.append(True)
        def wait(self): pass
    monkeypatch.setattr(download_cli, "AdaptivePacer", Pacer)
    downloads = []
    def fail(*args, **kwargs):
        downloads.append(args[0])
        return {"status": "failed", "error_category": "BOT_VERIFICATION", "error_message": "not a bot"}
    monkeypatch.setattr(download_cli, "_download_one", fail)
    assert download_cli.main([]) == 1
    assert len(downloads) == download_cli.BOT_STREAK_LIMIT
    assert len(backoffs) == download_cli.BOT_STREAK_LIMIT


def test_keyboard_interrupt_returns_cancel_status_not_success(tmp_path, monkeypatch, capsys):
    import download_cli
    args=download_cli.parse_args(['unused','-o',str(tmp_path),'--workers','1'])
    args.proxy_check_url=''
    monkeypatch.setattr(download_cli,'parse_args',lambda argv:args)
    job=download_cli.Job('https://example.com/a.mp4','paste',1,tmp_path)
    monkeypatch.setattr(download_cli,'_iter_sources',lambda *a:([job],[]))
    monkeypatch.setattr(download_cli,'collect_completed_files',lambda *a,**k:{})
    class InterruptedPool:
        def __init__(self,*a,**k):pass
        def __enter__(self):raise KeyboardInterrupt
        def __exit__(self,*a):pass
    monkeypatch.setattr(download_cli,'CompletionPool',InterruptedPool)
    assert download_cli.main([])==130
    assert '完成 success=' not in capsys.readouterr().out


def test_cli_bot_cutoff_records_running_and_unstarted_jobs(tmp_path, monkeypatch):
    """Cutoff must not silently lose outcomes from running or waiting jobs."""
    import threading
    import download_cli
    from batch_io import OverviewStore

    args = download_cli.parse_args([
        "unused", "-o", str(tmp_path), "--workers", "2",
        "--no-auto-recover", "--proxy-check-url", "",
    ])
    monkeypatch.setattr(download_cli, "parse_args", lambda argv: args)
    jobs = [
        download_cli.Job(f"https://example.com/{name}.mp4", "paste", i, tmp_path)
        for i, name in enumerate(("bot", "running", "notstarted"), start=1)
    ]
    monkeypatch.setattr(download_cli, "_iter_sources", lambda *a: (jobs, []))
    monkeypatch.setattr(download_cli, "collect_completed_files", lambda *a, **k: {})
    monkeypatch.setattr(download_cli, "BOT_STREAK_LIMIT", 1)

    class NoWaitPacer:
        def __init__(self, *a): pass
        def on_success(self): pass
        def on_skip(self): pass
        def on_bot_or_rate_limit(self): pass
        def wait(self): pass

    monkeypatch.setattr(download_cli, "AdaptivePacer", NoWaitPacer)
    running_started = threading.Event()
    allow_running_to_finish = threading.Event()
    original_cancel_pending = download_cli.CompletionPool.cancel_pending

    def release_running_then_cancel(self):
        # Force the running job to complete only after the cutoff is observed.
        allow_running_to_finish.set()
        return original_cancel_pending(self)

    monkeypatch.setattr(
        download_cli.CompletionPool, "cancel_pending", release_running_then_cancel,
    )
    called = []

    def fake_download(source, output_dir, *a, **kw):
        called.append(source)
        if source.endswith("/bot.mp4"):
            assert running_started.wait(3)
            return {
                "status": "failed", "error_category": "BOT_VERIFICATION",
                "error_message": "not a bot", "path": "",
            }
        running_started.set()
        assert allow_running_to_finish.wait(3)
        return {
            "status": "success", "error_category": "SUCCESS",
            "error_message": "", "path": str(tmp_path / "running.mp4"),
        }

    monkeypatch.setattr(download_cli, "_download_one", fake_download)
    assert download_cli.main([]) == 1
    rows = {row["video_id"]: row for row in OverviewStore(tmp_path).rows()}
    assert len(rows) == 3
    assert rows[jobs[0].media_id]["状态"] == "失败"
    assert rows[jobs[1].media_id]["状态"] == "成功"
    assert rows[jobs[2].media_id]["状态"] == "跳过"
    assert len(called) == 2


def test_cli_refuses_damaged_overview_before_download(tmp_path, monkeypatch):
    import download_cli
    from batch_io import OVERVIEW_NAME

    original = b"bad,data\\nvalue\\n"
    (tmp_path / OVERVIEW_NAME).write_bytes(original)
    args = download_cli.parse_args([
        "unused", "-o", str(tmp_path), "--proxy-check-url", "",
    ])
    monkeypatch.setattr(download_cli, "parse_args", lambda argv: args)
    monkeypatch.setattr(
        download_cli, "_iter_sources",
        lambda *a: ([download_cli.Job(
            "https://example.com/a.mp4", "paste", 1, tmp_path,
        )], []),
    )
    monkeypatch.setattr(download_cli, "_download_one", lambda *a, **k: (
        (_ for _ in ()).throw(AssertionError("download must not start"))
    ))
    assert download_cli.main([]) == 2
    assert (tmp_path / OVERVIEW_NAME).read_bytes() == original


def test_preview_does_not_create_output_log_or_cookie_snapshot(tmp_path, monkeypatch, capsys):
    import json
    import download_cli
    output = tmp_path / "unused" / "out"
    created = []

    monkeypatch.setattr(download_cli, "dump_browser_cookies",
                        lambda *a, **kw: created.append("cookies"))
    monkeypatch.setattr(download_cli, "check_exit_ip",
                        lambda *a: (_ for _ in ()).throw(AssertionError("network check")))
    code = download_cli.main([
        "--dry-run", "-o", str(output), "--cookies-from-browser", "chrome",
        "--proxy-check-url", "http://127.0.0.1:7897",
        "https://youtu.be/dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ",
    ])
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["total"] == 1  # pasted text parser already deduplicates
    assert result["download"] == 1
    assert result["reuse"] == 0
    assert not output.exists()
    assert not created


def test_preview_classifies_valid_existing_and_corrupt_media(tmp_path, monkeypatch, capsys):
    import json
    import download_cli
    from batch_io import OVERVIEW_NAME
    out = tmp_path / "output"
    paste = out / "paste"
    paste.mkdir(parents=True)
    (paste / "AAA111aaaaa.mp4").write_bytes(b"x" * 2048)
    (paste / "BBB222bbbbb.mp4").write_bytes(b"x" * 2048)
    monkeypatch.setattr(download_cli, "valid_resume_media",
                        lambda path, **kw: path.name.startswith("AAA"))
    result = download_cli.main([
        "--dry-run", "-o", str(out), "AAA111aaaaa", "BBB222bbbbb",
        "--proxy-check-url", "",
    ])
    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert {x["media_id"]: x["action"] for x in data["jobs"]} == {
        "AAA111aaaaa": "reuse", "BBB222bbbbb": "download",
    }
    assert not (out / OVERVIEW_NAME).exists()


def test_preview_refuses_bad_history_without_overwriting(tmp_path, capsys):
    import download_cli
    from batch_io import OVERVIEW_NAME
    original = b"this,is,not,a,valid,overview"
    out = tmp_path / "out"
    out.mkdir()
    (out / OVERVIEW_NAME).write_bytes(original)
    assert download_cli.main([
        "--dry-run", "-o", str(out), "AAA111aaaaa",
        "--proxy-check-url", "",
    ]) == 2
    assert (out / OVERVIEW_NAME).read_bytes() == original


def test_cli_closes_log_when_run_raises(tmp_path, monkeypatch):
    import logging
    import pytest
    import download_cli
    from logger_utils import AppLogger

    def boom(_args):
        raise RuntimeError("network failure")

    monkeypatch.setattr(download_cli, "_run", boom)
    try:
        with pytest.raises(RuntimeError, match="network failure"):
            download_cli.main(["-o", str(tmp_path), "AAA111aaaaa"])
        assert not any(
            isinstance(handler, logging.FileHandler)
            for handler in AppLogger.get_logger().handlers
        )
        (tmp_path / "youtube_downloader.log").unlink()
    finally:
        AppLogger.clear_handlers()


def test_cli_dry_run_json_on_cp1252_stdout(tmp_path, monkeypatch):
    import io
    import json
    import sys
    import download_cli

    target = io.BytesIO()
    stream = io.TextIOWrapper(target, encoding="cp1252", errors="strict", write_through=True)
    monkeypatch.setattr(sys, "stdout", stream)
    assert download_cli.main([
        "--dry-run", "-o", str(tmp_path / "out"), "AAA111aaaaa",
    ]) == 0
    parsed = json.loads(target.getvalue().decode("cp1252"))
    assert parsed["download"] == 1
