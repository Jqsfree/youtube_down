import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from download_cli import (  # noqa: E402
    AdaptivePacer,
    CLI_YDL_OPTS,
    collect_completed_ids,
    is_rotatable_clash_node,
    pick_next_clash_node,
    warn_datacenter_ip,
)


def test_collect_completed_ids_from_files_only(tmp_path: Path) -> None:
    out = tmp_path / "videos"
    out.mkdir()
    (out / "AAA111aaaaa.mp4").write_bytes(b"x" * 2048)
    (out / "tiny.mp4").write_bytes(b"no")

    done = collect_completed_ids(out)

    assert "AAA111aaaaa" in done
    assert "tiny" not in done


def test_cli_ydl_opts_never_zero_sleep() -> None:
    assert CLI_YDL_OPTS["sleep_interval"] >= 1
    assert CLI_YDL_OPTS["max_sleep_interval"] >= CLI_YDL_OPTS["sleep_interval"]
    assert CLI_YDL_OPTS["sleep_interval_requests"] >= 0.5
    assert CLI_YDL_OPTS["concurrent_fragment_downloads"] >= 8


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
