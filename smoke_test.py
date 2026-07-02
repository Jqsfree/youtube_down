"""CI smoke test — 验证 Windows 环境下 get_info 能正常工作。"""
from downloader import YoutubeDownloader, check_environment, clean_error

# 环境检测
ok, items = check_environment()
for i in items:
    print(f"  {i.status:7s} {i.name:20s} {i.version}")
assert ok, "Env check failed"

# get_info 测试
dl = YoutubeDownloader()
info = dl.get_info("dQw4w9WgXcQ")
print(f"OK: {info['title'][:40]} ({len(info.get('formats', []))} formats)")
