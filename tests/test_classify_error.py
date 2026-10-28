"""classify_error 关键词与优先级。"""

from downloader import classify_error


def test_bot_verification_matches_common_phrases() -> None:
    for msg in (
        "Sign in to confirm you're not a bot",
        "ERROR: Please confirm you're not a bot",
        "not a bot",
        "solve captcha to continue",
    ):
        cat = classify_error(Exception(msg))
        assert cat.code == "BOT_VERIFICATION", msg
        assert cat.retry_cookie is True


def test_rate_limit_matches_429_and_rate_limited() -> None:
    for msg in (
        "HTTP Error 429: Too Many Requests",
        "rate-limited by youtube",
        "This content isn't available, try again later",
    ):
        cat = classify_error(Exception(msg))
        assert cat.code == "RATE_LIMIT", msg
        assert cat.retry_cookie is False


def test_bot_outranks_generic_sign_in_when_not_a_bot() -> None:
    cat = classify_error(Exception("Sign in to confirm you're not a bot"))
    assert cat.code == "BOT_VERIFICATION"


def test_auth_required_age_gate() -> None:
    cat = classify_error(Exception("Sign in to confirm your age"))
    assert cat.code == "AUTH_REQUIRED"
    assert cat.retry_cookie is True


def test_unavailable_video_is_classified_not_unknown() -> None:
    cat = classify_error(Exception("ERROR: [youtube] zzzzzzzzzzz: This video is unavailable"))
    assert cat.code == "VIDEO_UNAVAILABLE"
    assert cat.retry_cookie is False


def test_proxy_failure_is_network_error() -> None:
    cat = classify_error(Exception("Unable to connect to proxy: Connection refused"))
    assert cat.code == "NETWORK_ERROR"
    assert cat.retry_cookie is False


def test_retry_backoff_is_capped() -> None:
    """下载路径也必须限制退避，否则单个坏视频可卡住整批几十分钟。"""
    from downloader import BRUTAL_YDL_OPTS, NORMAL_YDL_OPTS

    for opts in (NORMAL_YDL_OPTS, BRUTAL_YDL_OPTS):
        sleepers = opts["retry_sleep_functions"]
        assert sleepers["extractor"](10) == 30
        assert sleepers["fragment"](10) == 30
        assert sleepers["http"](10) == 60


def test_normal_ydl_opts_never_zero_sleep() -> None:
    from downloader import NORMAL_YDL_OPTS

    assert NORMAL_YDL_OPTS["sleep_interval"] >= 1
    assert NORMAL_YDL_OPTS["max_sleep_interval"] >= NORMAL_YDL_OPTS["sleep_interval"]
    assert NORMAL_YDL_OPTS["sleep_interval_requests"] >= 0.5
    assert NORMAL_YDL_OPTS["concurrent_fragment_downloads"] == 12
    assert NORMAL_YDL_OPTS["sleep_interval"] == 1
    assert NORMAL_YDL_OPTS["max_sleep_interval"] == 3
