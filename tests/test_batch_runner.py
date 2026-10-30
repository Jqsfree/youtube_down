from pathlib import Path
import threading
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


class FakeDownloader:
    def __init__(self, cookie=False):
        self._cancelled = False
        self.cookie = cookie
        self.calls = []
    def clear_cancel(self): self._cancelled = False
    def _is_cancelled(self): return self._cancelled
    def has_cookie_for_source(self, source): return self.cookie
    def uses_browser_cookies_only(self): return False
    def get_info(self, source, use_cookies=False, **kwargs):
        import yt_dlp
        self.calls.append(('info', source, use_cookies))
        if self.cookie and not use_cookies:
            raise yt_dlp.utils.DownloadError('login required')
        return {'title':'demo', '_platform':'generic', 'formats': []}
    def list_formats(self, **kwargs): return []
    def download(self, **kwargs):
        self.calls.append(('download', kwargs['video_id'], kwargs['use_cookies']))
        return kwargs['output_dir'] / 'fake.mp4'


def runner(tmp_path, ids=None, downloader=None, callbacks=None):
    from batch_runner import BatchRunner
    from types import SimpleNamespace
    result = BatchRunner(downloader or FakeDownloader(), ids or [], 'auto', tmp_path, callbacks=callbacks)
    result._make_pacer = lambda: SimpleNamespace(on_success=lambda: None, on_skip=lambda: None, on_bot_or_rate_limit=lambda: None, wait=lambda: None)
    return result


def test_runner_is_usable_without_loading_qt():
    subprocess.run([sys.executable,'-c',
        "import batch_runner,sys; assert not any(n.startswith('PySide6') for n in sys.modules)"],
        cwd=ROOT, check=True, capture_output=True)


def test_plain_runner_keeps_two_stage_cookie_and_records_result(tmp_path):
    dl=FakeDownloader(cookie=True)
    finished=[]
    r=runner(tmp_path,['https://example.com/a.mp4'],dl,{'all_finished':lambda *args:finished.append(args)})
    r.run()
    assert [call[2] for call in dl.calls if call[0]=='info'] == [False,True]
    assert finished[0][:3] == (1,0,0)
    assert r.overview_path.is_file()


def test_empty_runner_finishes_without_downloading(tmp_path):
    finished=[]
    r=runner(tmp_path, callbacks={'all_finished':lambda *args:finished.append(args)})
    r.run()
    assert finished[0][:3] == (0,0,0)


def test_completion_pool_reports_completion_order_and_drains(tmp_path):
    from batch_runner import CompletionPool
    release=threading.Event()
    second_done=threading.Event()
    def work(item):
        if item=='first': assert release.wait(3)
        else: second_done.set()
        return item
    try:
        with CompletionPool(2) as pool:
            pool.submit('first',work,'first')
            pool.submit('second',work,'second')
            assert second_done.wait(3)
            future=next(pool.completed())
            assert pool.pop(future)=='second'
            assert future.result()=='second'
            release.set()
            future=next(pool.completed())
            assert pool.pop(future)=='first'
    finally:
        release.set()


def test_live_enqueue_deduplicates_and_finishes_without_idle_sleep(tmp_path):
    started=threading.Event();release=threading.Event();finished=[];totals=[]
    r=runner(tmp_path,['https://example.com/a.mp4'])
    def done(index,*args):
        if index==0:
            started.set(); assert release.wait(3)
        else: r.close_queue()
    r._callbacks={'video_finished':done,'total_changed':totals.append,'all_finished':lambda *args:finished.append(args)}
    r.enable_live_queue()
    thread=threading.Thread(target=r.run);thread.start()
    try:
        assert started.wait(3)
        assert r.enqueue(['https://example.com/a.mp4','https://example.com/b.mp4'])
        release.set();thread.join(3)
        assert not thread.is_alive()
        assert totals==[2] and finished[0][:3]==(2,0,0)
        assert not r.enqueue(['https://example.com/c.mp4'])
    finally:
        release.set();r.close_queue();thread.join(3)


def test_pause_then_resume_blocks_at_job_boundary(tmp_path):
    r=runner(tmp_path,['https://example.com/a.mp4'])
    waiting=threading.Event();original=r._pause_event.wait
    r._pause_event.wait=lambda timeout=None: (waiting.set(),original(timeout))[1]
    r.pause();thread=threading.Thread(target=r.run);thread.start()
    try:
        assert waiting.wait(3)
        assert r._downloader.calls==[]
        r.resume();thread.join(3)
        assert not thread.is_alive() and r._downloader.calls
    finally:
        r.resume();r.close_queue();thread.join(3)


def test_cancel_wakes_empty_live_queue_without_reporting_100(tmp_path):
    progress=[];waiting=threading.Event();r=runner(tmp_path,callbacks={'all_progress_changed':progress.append})
    r.enable_live_queue();r._idle_exit_seconds=30
    original=r._live_cond.wait
    r._live_cond.wait=lambda timeout=None: (waiting.set(),original(timeout))[1]
    thread=threading.Thread(target=r.run);thread.start()
    try:
        assert waiting.wait(3)
        r._downloader._cancelled=True;r.close_queue();thread.join(3)
        assert not thread.is_alive() and 100 not in progress
    finally:
        r.close_queue();thread.join(3)


def test_enqueue_losing_natural_exit_race_is_rejected(tmp_path):
    r=runner(tmp_path);r.enable_live_queue();r._idle_exit_seconds=0
    boundary=threading.Event();release=threading.Event();enqueuing=threading.Event();accepted=[]
    count=0
    def cancelled():
        nonlocal count
        count+=1
        if count==2:
            boundary.set(); assert release.wait(3)
        return False
    r._is_cancelled=cancelled
    r._live_cond.wait=lambda timeout=None: None
    exiting=threading.Thread(target=r._next_live_item);exiting.start()
    def add():
        enqueuing.set();accepted.append(r.enqueue(['https://example.com/a.mp4']))
    adding=threading.Thread(target=add)
    try:
        assert boundary.wait(3);adding.start();assert enqueuing.wait(3)
        release.set();exiting.join(3);adding.join(3)
        assert accepted==[False] and not r._live
    finally:
        release.set();exiting.join(3)
        if adding.ident: adding.join(3)


def test_record_failure_closes_admission_and_reports_failure(tmp_path,monkeypatch):
    finished=[];r=runner(tmp_path,['https://example.com/a.mp4'],callbacks={'all_finished':lambda *args:finished.append(args)})
    r.enable_live_queue()
    original_download = r._downloader.download
    def downloaded(**kwargs):
        path = original_download(**kwargs)
        path.write_bytes(b'preserved fake media' * 100)
        return path
    monkeypatch.setattr(r._downloader, 'download', downloaded)
    def fail(**kwargs): raise OSError('overview disk full')
    monkeypatch.setattr(r._overview,'upsert',fail)
    with pytest.raises(OSError,match='disk full'):
        r.run()
    assert not r.enqueue(['https://example.com/b.mp4'])
    assert 'disk full' in r._fatal_error
    assert finished[0][:3]==(0,1,0)
    assert (tmp_path/'fake.mp4').read_bytes()==b'preserved fake media' * 100


def test_browser_only_source_keeps_single_worker(tmp_path):
    r=runner(tmp_path)
    r._cookie_overrides={'a':{'cookiefile':'fake.txt'},'b':{'cookies_from_browser':'chrome'}}
    assert r._brutal_max_workers()==1


@pytest.mark.parametrize('cancel_waiter', [False, True])
def test_late_browser_jobs_are_serialized_and_cancel_waiters(tmp_path,cancel_waiter):
    r=runner(tmp_path)
    assert r._brutal_max_workers()>1
    r._cookie_overrides={'a':{'cookies_from_browser':'chrome'},'b':{'cookies_from_browser':'chrome'}}
    first=threading.Event();release=threading.Event();second_acquiring=threading.Event()
    mutex=threading.Lock();counts=threading.Lock();entered=0;active=0;peak=0;calls=[];rows=[]
    class ObservedLock:
        def __enter__(self):
            nonlocal entered
            with counts:
                entered+=1
                if entered==2: second_acquiring.set()
            mutex.acquire()
        def __exit__(self,*args): mutex.release()
    r._browser_cookie_lock=ObservedLock()
    def execute(index,vid,total):
        nonlocal active,peak
        with counts:
            calls.append(index);active+=1;peak=max(peak,active)
        if index==0:
            first.set();assert release.wait(10)
        with counts: active-=1
        return r._result_row(vid,'success','SUCCESS','fake.mp4',False,'')
    r._process_one_video_brutal_unlocked=execute
    one=threading.Thread(target=lambda: rows.append(r._process_one_video_brutal(0,'a',2)))
    two=threading.Thread(target=lambda: rows.append(r._process_one_video_brutal(1,'b',2)))
    one.start()
    try:
        assert first.wait(3);two.start();assert second_acquiring.wait(3)
        if cancel_waiter:r._downloader._cancelled=True
        release.set();one.join(3);two.join(3)
        assert not one.is_alive() and not two.is_alive() and len(rows)==2
        assert peak==1
        assert calls==([0] if cancel_waiter else [0,1])
        if cancel_waiter: assert any(row['error_category']=='CANCELLED' for row in rows)
    finally:
        release.set();one.join(3)
        if two.ident:two.join(3)


def test_direct_policy_keeps_best_and_rate_retry_delay(tmp_path,monkeypatch):
    import batch_runner
    import yt_dlp
    calls=[];waits=[]
    class Downloader:
        def download(self,**kwargs):
            calls.append(kwargs)
            if len(calls)==1:raise yt_dlp.utils.DownloadError('HTTP Error 429: Too Many Requests')
            return tmp_path/'ok.mp4'
    monkeypatch.setattr(batch_runner.time,'sleep',waits.append)
    row=batch_runner.download_direct_item(Downloader(),'https://example.com/a.mp4',tmp_path,720)
    assert row['status']=='success' and waits==[10]
    assert all(call['format_id']=='best' and call['min_height']==0 for call in calls)


def test_parallel_transient_record_failure_is_fatal_not_overwritten(tmp_path,monkeypatch):
    r=runner(tmp_path,['a','b']);r._brutal_mode=True;r._brutal_max_workers=lambda:1
    calls=[];writes=[];original=r._overview.upsert;failure=OSError('transient overview failure')
    def job(index,vid,total):
        calls.append(vid)
        (tmp_path/f'{vid}.mp4').write_bytes(b'preserved')
        return r._result_row(vid,'success','SUCCESS',str(tmp_path/f'{vid}.mp4'),False,'')
    def write(**kwargs):
        writes.append(kwargs)
        if len(writes)==1:raise failure
        original(**kwargs)
    r._process_one_video_brutal=job
    monkeypatch.setattr(r._overview,'upsert',write)
    with pytest.raises(OSError) as caught:r.run()
    assert caught.value is failure
    assert calls==['a'] and len(writes)==1
    assert (tmp_path/'a.mp4').read_bytes()==b'preserved'


def test_parallel_bot_cutoff_drains_live_backlog_without_hanging(tmp_path,monkeypatch):
    import batch_runner
    monkeypatch.setattr(batch_runner,'BOT_STREAK_LIMIT',1)
    r=runner(tmp_path,['a']);r._brutal_mode=True;r._brutal_max_workers=lambda:1
    finished=[];r._callbacks={'all_finished':lambda *args:finished.append(args)}
    r._process_one_video_brutal=lambda index,vid,total:r._result_row(vid,'failed','BOT_VERIFICATION','captcha',False,'')
    r.enable_live_queue();assert r.enqueue(['b','c'])
    thread=threading.Thread(target=r.run);thread.start()
    try:
        thread.join(3)
        assert not thread.is_alive()
        assert finished[0][:3]==(0,1,2)
        assert r._overview.status_of(r._source_rel,'b')=='skipped'
        assert r._overview.status_of(r._source_rel,'c')=='skipped'
    finally:
        r.close_queue();thread.join(3)


def test_sequential_bot_cutoff_records_live_backlog(tmp_path,monkeypatch):
    import batch_runner
    monkeypatch.setattr(batch_runner,'BOT_STREAK_LIMIT',1)
    r=runner(tmp_path,['a']);finished=[]
    r._callbacks={'all_finished':lambda *args:finished.append(args)}
    r._process_one_video=lambda index,vid,total:r._result_row(vid,'failed','BOT_VERIFICATION','captcha',False,'')
    r.enable_live_queue();assert r.enqueue(['b','c'])
    r.run()
    assert finished[0][:3]==(0,1,2)
    assert r._overview.status_of(r._source_rel,'b')=='skipped'
    assert r._overview.status_of(r._source_rel,'c')=='skipped'


def test_cancel_during_rate_backoff_does_not_start_another_extraction(tmp_path,monkeypatch):
    import batch_runner
    import yt_dlp
    r=runner(tmp_path);calls=[]
    def limited(*args,**kwargs):
        calls.append(True)
        raise yt_dlp.utils.DownloadError('HTTP Error 429: Too Many Requests')
    r._downloader.get_info=limited
    monkeypatch.setattr(batch_runner.time,'sleep',lambda seconds:setattr(r._downloader,'_cancelled',True))
    r._try_download('https://example.com/a.mp4',0,1)
    assert len(calls)==1


def test_worker_business_methods_are_inherited_from_pure_core():
    from worker import BatchDownloadWorker
    from batch_runner import BatchRunnerCore
    for name in ['_run_sequential','_run_brutal_parallel','_try_download','enqueue','pause']:
        assert getattr(BatchDownloadWorker,name) is getattr(BatchRunnerCore,name)


def test_platform_cookie_file_does_not_hide_browser_for_other_platform(tmp_path):
    from downloader import YoutubeDownloader
    dl=YoutubeDownloader.__new__(YoutubeDownloader)
    dl._cookiefile_path=None;dl._cookiefile_platform=None
    dl._platform_cookiefiles={'bilibili':tmp_path/'fake.txt'}
    dl._cookies_spec='chrome';dl._browser_cookie_broken=False
    r=runner(tmp_path,['https://www.youtube.com/watch?v=dQw4w9WgXcQ'],dl)
    assert r._brutal_max_workers()==1


def test_browser_lock_failure_in_clone_is_remembered_for_batch(tmp_path,monkeypatch):
    import downloader
    import yt_dlp
    dl=downloader.YoutubeDownloader(cookies_from_browser='chrome:Default')
    r=runner(tmp_path,['aaaaaaaaaaa','bbbbbbbbbbb'],dl)
    browser_reads=[]
    class YDL:
        def __init__(self,opts): self.opts=opts
        def __enter__(self):
            if self.opts.get('cookiesfrombrowser'):
                browser_reads.append(True)
                raise RuntimeError('copy chrome cookie database')
            return self
        def __exit__(self,*args):pass
        def extract_info(self,*args,**kwargs):raise yt_dlp.utils.DownloadError('login required')
    monkeypatch.setattr(downloader,'_open_ydl',lambda opts,platform=None:YDL(opts))
    r._process_one_video_brutal(0,'aaaaaaaaaaa',2)
    r._process_one_video_brutal(1,'bbbbbbbbbbb',2)
    assert browser_reads==[True]
    assert dl._browser_cookie_broken
