"""从 ``assets/icon.svg`` 生成打包与运行时需要的图标文件。

用法::

    python scripts/make_icon.py            # 生成 png / ico（+ macOS 上生成 icns）
    python scripts/make_icon.py --check    # 只检查现有资产

产物（全部提交进仓库）：
    assets/icon.png       1024x1024
    assets/icon.ico       16/24/32/48/64/128/256 多尺寸（PNG-in-ICO）
    assets/icon.icns      macOS（仅在 macOS 上生成，需要 iconutil）
"""

from __future__ import annotations

import argparse
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SVG = ASSETS / "icon.svg"
PNG = ASSETS / "icon.png"
ICO = ASSETS / "icon.ico"
ICNS = ASSETS / "icon.icns"

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_ENTRIES = (
    ("icp4", 16), ("icp5", 32), ("icp6", 64),
    ("ic07", 128), ("ic08", 256), ("ic09", 512), ("ic10", 1024),
)


def _require_pyqt() -> None:
    """提前给出可读的依赖缺失提示。"""
    try:
        import PySide6.QtSvg  # noqa: F401, PLC0415
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise SystemExit(f"需要 PySide6 渲染 SVG: {exc}") from exc


def render_png(size: int, out: Path) -> None:
    """用 QSvgRenderer 把 SVG 渲染成指定尺寸的 PNG。"""
    from PySide6.QtCore import QRectF, Qt  # noqa: PLC0415
    from PySide6.QtGui import QImage, QPainter  # noqa: PLC0415
    from PySide6.QtSvg import QSvgRenderer  # noqa: PLC0415

    renderer = QSvgRenderer(str(SVG))
    if not renderer.isValid():
        raise SystemExit(f"无法解析 SVG: {SVG}")
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    out.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(out), "PNG"):
        raise SystemExit(f"写入失败: {out}")


def write_ico(pngs: dict[int, Path], out: Path) -> None:
    """把多张 PNG 打包成 ICO（Vista+ 支持 PNG 压缩帧，无需 Pillow）。"""
    entries = sorted(pngs.items())
    header = struct.pack("<HHH", 0, 1, len(entries))
    body = b""
    offset = len(header) + 16 * len(entries)
    directory = b""
    for size, path in entries:
        data = path.read_bytes()
        dim = 0 if size >= 256 else size
        directory += struct.pack(
            "<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset,
        )
        body += data
        offset += len(data)
    out.write_bytes(header + directory + body)


def write_icns(pngs: dict[int, Path], out: Path) -> None:
    """用 iconutil 生成 icns（仅 macOS）。"""
    if not shutil.which("iconutil"):
        print("[icon] 跳过 icns：当前系统没有 iconutil", file=sys.stderr)
        return
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        mapping = {
            16: ["icon_16x16.png"],
            32: ["icon_16x16@2x.png", "icon_32x32.png"],
            64: ["icon_32x32@2x.png"],
            128: ["icon_128x128.png"],
            256: ["icon_128x128@2x.png", "icon_256x256.png"],
            512: ["icon_256x256@2x.png", "icon_512x512.png"],
            1024: ["icon_512x512@2x.png"],
        }
        for size, names in mapping.items():
            src = pngs.get(size)
            if src is None:
                continue
            for name in names:
                shutil.copyfile(src, iconset / name)
        subprocess.run(  # noqa: S603 - 固定命令
            ["iconutil", "-c", "icns", str(iconset), "-o", str(out)],
            check=True,
        )
        print(f"[icon] 已生成 {out.relative_to(ROOT)}")


def build() -> None:
    if not SVG.is_file():
        raise SystemExit(f"缺少源文件: {SVG}")
    _require_pyqt()
    ASSETS.mkdir(parents=True, exist_ok=True)
    pngs: dict[int, Path] = {}
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        for size in sorted(set(ICO_SIZES) | {512, 1024}):
            target = PNG if size == 1024 else tmpdir / f"icon-{size}.png"
            render_png(size, target)
            pngs[size] = target
        write_ico({s: pngs[s] for s in ICO_SIZES}, ICO)
        print(f"[icon] 已生成 {PNG.relative_to(ROOT)} / {ICO.relative_to(ROOT)}")
        if sys.platform == "darwin":
            write_icns(pngs, ICNS)


def check() -> int:
    """校验资产是否存在且看起来合法。"""
    ok = True
    for path in (SVG, PNG, ICO):
        exists = path.is_file() and path.stat().st_size > 0
        print(f"[icon] {'OK ' if exists else 'MISS'} {path.relative_to(ROOT)}")
        ok &= exists
    if not ICNS.is_file():
        print(f"[icon] MISS {ICNS.relative_to(ROOT)}（非 macOS 构建可忽略）")
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="生成应用图标资产")
    parser.add_argument("--check", action="store_true", help="只校验现有资产")
    args = parser.parse_args()
    if args.check:
        raise SystemExit(check())
    build()


if __name__ == "__main__":
    main()
