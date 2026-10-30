import logging
from pathlib import Path

import pytest

from logger_utils import AppLogger


@pytest.fixture(autouse=True)
def reset_logger():
    AppLogger.clear_handlers()
    yield
    AppLogger.clear_handlers()


def test_log_redacts_credentials_in_messages_and_exceptions(tmp_path):
    path = tmp_path / 'app.log'
    AppLogger.attach_file(path)
    logger = AppLogger.get_logger()
    samples = [
        'proxy http://user:FAKE_PROXY@localhost:80',
        'https://example.com/video?token=FAKE_QUERY',
        'Cookie: SID=FAKE_COOKIE',
        '{"Authorization": "Bearer FAKE_AUTH"}',
        'cookiefile=C:\\Users\\fake user\\cookies.txt',
        'token=FAKE_TOKEN',
    ]
    for text in samples:
        logger.info('%s', text)
        try:
            raise RuntimeError(text)
        except RuntimeError as exc:
            AppLogger.log_exception(exc, 'failed')
    content = path.read_text(encoding='utf-8')
    for secret in ['FAKE_PROXY','FAKE_QUERY','FAKE_COOKIE','FAKE_AUTH','FAKE_TOKEN','fake user']:
        assert secret not in content
    assert 'RuntimeError' in content


def test_rotated_logs_have_a_fixed_retention_limit(tmp_path, monkeypatch):
    import logger_utils
    monkeypatch.setattr(logger_utils, 'MAX_LOG_BYTES', 256)
    monkeypatch.setattr(logger_utils, 'LOG_BACKUPS', 2)
    path = tmp_path / 'app.log'
    AppLogger.attach_file(path)
    for _ in range(80):
        AppLogger.get_logger().info('bounded message %s', 'x' * 40)
    files = list(tmp_path.iterdir())
    assert len(files) == 3
    assert all(file.stat().st_size <= 256 for file in files)


def test_log_open_or_rollover_failure_does_not_escape(tmp_path, monkeypatch):
    path = tmp_path / 'app.log'
    AppLogger.attach_file(path)
    handler = next(h for h in AppLogger.get_logger().handlers if isinstance(h, logging.FileHandler))
    def fail(*args, **kwargs): raise OSError('disk full')
    monkeypatch.setattr(handler, 'shouldRollover', lambda record: True)
    monkeypatch.setattr(handler, 'doRollover', fail)
    AppLogger.get_logger().warning('download continues')
    monkeypatch.setattr(Path, 'mkdir', fail)
    assert AppLogger.attach_file(tmp_path / 'missing' / 'new.log') is False


def test_engine_debug_is_file_only_and_never_logs_raw_response(tmp_path):
    from downloader import _EngineLogger
    path = tmp_path / 'app.log'
    AppLogger.attach_file(path)
    adapter = _EngineLogger('youtube', 'extract')
    adapter.debug('HTTP headers: X-Custom: FAKE_HEADER_VALUE')
    adapter.error('<html>FAKE_RESPONSE_BODY token=FAKE_TOKEN</html>')
    text = path.read_text(encoding='utf-8')
    assert 'FAKE_' not in text
    assert 'platform=youtube' in text and 'task=' in text
    assert 'stage=' in text and 'category=' in text


def test_cookie_status_without_credentials_remains_readable():
    from logger_utils import redact
    assert redact('Cookie 验证通过') == 'Cookie 验证通过'


def test_cli_console_uses_same_redaction(capsys):
    import download_cli
    download_cli._print('proxy', 'http://user:FAKE_PASS@localhost:80', flush=True)
    assert 'FAKE_PASS' not in capsys.readouterr().out


def test_gui_receives_redacted_info_but_no_engine_debug(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    messages = []
    AppLogger.attach_gui(messages.append)
    AppLogger.get_logger().debug('file-only engine debug')
    AppLogger.get_logger().info('proxy https://user:FAKE_GUI_PASS@example.com')
    app.processEvents()
    assert len(messages) == 1
    assert 'FAKE_GUI_PASS' not in messages[0]


def test_failed_log_switch_does_not_write_into_previous_session(tmp_path, monkeypatch):
    first = tmp_path / 'first.log'
    AppLogger.attach_file(first)
    AppLogger.get_logger().info('first session')
    def fail(*args, **kwargs): raise OSError('read only')
    monkeypatch.setattr(Path, 'mkdir', fail)
    assert AppLogger.attach_file(tmp_path / 'other' / 'second.log') is False
    AppLogger.get_logger().info('second session')
    assert 'second session' not in first.read_text(encoding='utf-8')


def test_option_build_error_preserves_original_cause(monkeypatch):
    from downloader import YoutubeDownloader, classify_error
    from yt_dlp.utils import DownloadError
    downloader = YoutubeDownloader(cookies_from_browser='')
    def fail(**kwargs): raise RuntimeError('ffmpeg not found')
    monkeypatch.setattr(downloader, '_make_opts', fail)
    with pytest.raises(DownloadError) as caught:
        downloader.get_info('dQw4w9WgXcQ', use_cookies=False)
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert classify_error(caught.value).code == 'ENVIRONMENT'
