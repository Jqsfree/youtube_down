"""Isolated real transfer test: synthesized media through local HTTP.

Only on a release-cut branch; does NOT validate external YouTube availability.
"""
from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import threading

from media_probe import NO_WINDOW, find_tool
from scripts.real_download_smoke import verify


def main() -> None:
    with TemporaryDirectory(prefix="ytdown-local-e2e-") as root_name:
        root = Path(root_name)
        media = root / "serving"
        media.mkdir()
        clip = media / "test.mp4"
        subprocess.run(
            [
                find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=320x240:r=24",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                "-t", "2", "-c:v", "mpeg4", "-q:v", "4",
                "-c:a", "aac", "-b:a", "96k", "-shortest",
                "-y", str(clip),
            ],
            check=True, creationflags=NO_WINDOW, timeout=30,
        )
        assert clip.stat().st_size > 1024
        handler = partial(SimpleHTTPRequestHandler, directory=str(media))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/test.mp4"
            verify(url, root / "output")
            print("PASS: local HTTP transfer, ffprobe, CSV, resume")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
